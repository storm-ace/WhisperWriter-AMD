import io
import re
import numpy as np
import requests
import soundfile as sf

from utils import ConfigManager


def build_initial_prompt(common):
    """Initial prompt plus the project vocabulary, so names and file names land right."""
    parts = []
    if common.get('initial_prompt'):
        parts.append(common['initial_prompt'].strip())
    vocab = [w.strip() for w in (common.get('vocabulary') or '').replace('\n', ',').split(',')]
    vocab = [w for w in vocab if w]
    if vocab:
        parts.append(', '.join(vocab) + '.')
    return ' '.join(parts) or None


def resolve_engine():
    """Return the active transcription engine.

    'whispercpp' = GPU via a local whisper.cpp server over HTTP (Vulkan).
    'faster-whisper' = in-process, CPU.
    """
    return ConfigManager.get_config_value('model_options', 'engine') or 'faster-whisper'

def create_local_model():
    """
    Create a local model using the faster-whisper library.
    """
    # Imported lazily: faster-whisper pulls in ctranslate2/onnxruntime, which costs seconds
    # of startup and is not needed at all for the whisper.cpp engine.
    from faster_whisper import WhisperModel

    ConfigManager.console_print('Creating local model...')
    local_model_options = ConfigManager.get_config_section('model_options')['local']
    compute_type = local_model_options['compute_type']
    model_path = local_model_options.get('model_path')

    if compute_type == 'int8':
        device = 'cpu'
        ConfigManager.console_print('Using int8 quantization, forcing CPU usage.')
    else:
        device = local_model_options['device']

    try:
        if model_path:
            ConfigManager.console_print(f'Loading model from: {model_path}')
            model = WhisperModel(model_path,
                                 device=device,
                                 compute_type=compute_type,
                                 download_root=None)  # Prevent automatic download
        else:
            model = WhisperModel(local_model_options['model'],
                                 device=device,
                                 compute_type=compute_type)
    except Exception as e:
        ConfigManager.console_print(f'Error initializing WhisperModel: {e}')
        ConfigManager.console_print('Falling back to CPU.')
        model = WhisperModel(model_path or local_model_options['model'],
                             device='cpu',
                             compute_type=compute_type,
                             download_root=None if model_path else None)

    ConfigManager.console_print('Local model created.')
    return model

def transcribe_local(audio_data, local_model=None):
    """
    Transcribe an audio file using a local model.
    """
    if not local_model:
        local_model = create_local_model()
    model_options = ConfigManager.get_config_section('model_options')

    # Convert int16 to float32
    audio_data_float = audio_data.astype(np.float32) / 32768.0

    response = local_model.transcribe(audio=audio_data_float,
                                      language=model_options['common']['language'],
                                      initial_prompt=build_initial_prompt(model_options['common']),
                                      condition_on_previous_text=model_options['local']['condition_on_previous_text'],
                                      temperature=model_options['common']['temperature'],
                                      vad_filter=model_options['local']['vad_filter'],)
    return ''.join([segment.text for segment in list(response[0])])

def transcribe_whispercpp(audio_data):
    """
    Transcribe via a local whisper.cpp `whisper-server` (GPU/Vulkan) over HTTP.

    The int16 audio is sent straight as a WAV; the /32768.0 conversion used by the
    in-process engine is not needed here (whisper.cpp decodes the WAV itself).
    """
    model_options = ConfigManager.get_config_section('model_options')
    wc_options = model_options.get('whispercpp', {})
    host = wc_options.get('host') or '127.0.0.1'
    port = int(wc_options.get('port') or 8080)
    server_url = f'http://{host}:{port}'

    byte_io = io.BytesIO()
    sample_rate = ConfigManager.get_config_section('recording_options').get('sample_rate') or 16000
    sf.write(byte_io, audio_data, sample_rate, format='wav')
    byte_io.seek(0)

    data = {
        'temperature': str(model_options['common']['temperature']),
        'response_format': 'json',
        # Fewer invented words on noise: suppress non-speech tokens and drop segments
        # the model itself rates as probably silent.
        'suppress_nst': 'true',
        'no_speech_thold': '0.6',
    }
    language = model_options['common'].get('language')
    if language:
        data['language'] = language
    initial_prompt = build_initial_prompt(model_options['common'])
    if initial_prompt:
        data['prompt'] = initial_prompt

    response = requests.post(
        server_url.rstrip('/') + '/inference',
        files={'file': ('audio.wav', byte_io, 'audio/wav')},
        data=data,
        timeout=120,
    )
    response.raise_for_status()
    return response.json().get('text', '')


DEFAULT_LLM_SYSTEM_PROMPT = (
    "Je bent een spellingscorrector voor gedicteerde tekst. Geef ALLEEN de gecorrigeerde "
    "tekst terug: fix interpunctie, hoofdletters en duidelijk verkeerd verstane woorden. "
    "Behoud exact dezelfde woorden en betekenis; herschrijf niet, vat niet samen, voeg niets toe. "
    "Als de tekst een vraag is, corrigeer je alleen de schrijfwijze en beantwoord je de vraag NIET. "
    "Antwoord uitsluitend met de gecorrigeerde tekst, zonder aanhalingstekens of uitleg. "
    "Behoud de taal (Nederlands of Engels)."
)


def correct_with_llm(text, opts):
    """
    Clean up a transcription via a local llama.cpp server (GPU). Fail-open: on any
    error, timeout or empty reply, the original text is returned unchanged so a problem
    with the LLM never blocks dictation.
    """
    try:
        host = opts.get('host') or '127.0.0.1'
        port = int(opts.get('port') or 8081)
        system = opts.get('system_prompt') or DEFAULT_LLM_SYSTEM_PROMPT
        body = {
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': text},
            ],
            'temperature': float(opts.get('temperature') or 0.0),
            'max_tokens': len(text.split()) * 3 + 24,
            'cache_prompt': True,
        }
        resp = requests.post(f'http://{host}:{port}/v1/chat/completions',
                             json=body, timeout=float(opts.get('timeout_s') or 8))
        resp.raise_for_status()
        out = (resp.json()['choices'][0]['message']['content'] or '').strip()
        return out or text
    except Exception as e:
        ConfigManager.console_print(f'LLM correction skipped ({e}); using raw text.')
        return text


def apply_word_replacements(text, rules_text):
    """Apply user-defined 'wrong = right' replacements (whole-word, case-insensitive).

    One rule per line, in the form 'wrong = right' or 'wrong -> right'. Used to fix
    words the model consistently mishears (names, brands, jargon).
    """
    for line in (rules_text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        sep = '->' if '->' in line else ('=' if '=' in line else None)
        if not sep:
            continue
        wrong, right = line.split(sep, 1)
        wrong, right = wrong.strip(), right.strip()
        if not wrong:
            continue
        text = re.sub(r'\b' + re.escape(wrong) + r'\b', right, text, flags=re.IGNORECASE)
    return text


# Whisper "hears" these in silence or noise (YouTube-subtitle training data). A transcript
# that consists of nothing but one of them is dropped.
HALLUCINATIONS = {
    'thank you', 'thank you very much', 'thanks', 'thanks for watching',
    'thank you for watching', 'thanks for listening', 'please subscribe',
    'subscribe to my channel', 'like and subscribe', 'you', 'bye', 'bye bye', 'the end',
    'so', 'uh', 'um', 'hmm', 'oh', 'okay bye', 'see you next time',
    'dank je wel', 'dankjewel', 'bedankt', 'bedankt voor het kijken', 'tot de volgende keer',
    'ondertiteling door de amaraorg gemeenschap', 'ondertitels ingediend door de amaraorg gemeenschap',
    'ondertiteld door de amaraorg gemeenschap', 'amaraorg', 'tv gelderland 2021',
    'subtitles by the amaraorg community',
}

_NON_SPEECH_TAGS = re.compile(r'\[(?:blank_audio|silence|music|noise|applause|laughter|inaudible)[^\]]*\]'
                              r'|\((?:silence|music|noise|applause|laughter|inaudible)[^)]*\)'
                              r'|[♪♫]+', re.IGNORECASE)


def remove_hallucinations(text):
    """Strip non-speech tags ([BLANK_AUDIO], (music)) and drop known silence phrases."""
    text = _NON_SPEECH_TAGS.sub('', text).strip()
    key = re.sub(r'[^\w\s]', '', text.lower())
    key = re.sub(r'\s+', ' ', key).strip()
    if not key or key in HALLUCINATIONS:
        return ''
    return text


def post_process_transcription(transcription, info=None):
    """
    Apply post-processing to the transcription. When `info` is a dict, it receives
    'corrected' = True if the LLM changed the text.
    """
    transcription = transcription.strip()
    post_processing = ConfigManager.get_config_section('post_processing')
    if post_processing.get('filter_hallucinations', True):
        transcription = remove_hallucinations(transcription)
        if not transcription:
            return ''
    transcription = apply_word_replacements(transcription, post_processing.get('word_replacements'))

    llm = post_processing.get('llm_correction') or {}
    if llm.get('enabled') and transcription:
        corrected = correct_with_llm(transcription, llm)
        if info is not None:
            info['corrected'] = corrected != transcription
        transcription = corrected

    if post_processing['remove_trailing_period'] and transcription.endswith('.'):
        transcription = transcription[:-1]
    if post_processing['add_trailing_space']:
        transcription += ' '
    if post_processing['remove_capitalization']:
        transcription = transcription.lower()

    return transcription

def transcribe_detailed(audio_data, local_model=None):
    """
    Transcribe audio with the configured engine and return a dict:
    text (post-processed), raw (engine output), corrected (LLM changed it).
    """
    if audio_data is None:
        return {'text': '', 'raw': '', 'corrected': False}

    if resolve_engine() == 'whispercpp':
        raw = transcribe_whispercpp(audio_data)
    else:  # 'faster-whisper'
        raw = transcribe_local(audio_data, local_model)

    info = {'corrected': False}
    text = post_process_transcription(raw, info)
    return {'text': text, 'raw': raw.strip(), 'corrected': info['corrected']}

def transcribe(audio_data, local_model=None):
    """Transcribe audio and return only the post-processed text."""
    return transcribe_detailed(audio_data, local_model)['text']

