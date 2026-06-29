import io
import numpy as np
import requests
import soundfile as sf
from faster_whisper import WhisperModel

from utils import ConfigManager


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
                                      initial_prompt=model_options['common']['initial_prompt'],
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
    }
    language = model_options['common'].get('language')
    if language:
        data['language'] = language
    initial_prompt = model_options['common'].get('initial_prompt')
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


def apply_word_replacements(text, rules_text):
    """Apply user-defined 'wrong = right' replacements (whole-word, case-insensitive).

    One rule per line, in the form 'wrong = right' or 'wrong -> right'. Used to fix
    words the model consistently mishears (names, brands, jargon).
    """
    import re
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


def post_process_transcription(transcription):
    """
    Apply post-processing to the transcription.
    """
    transcription = transcription.strip()
    post_processing = ConfigManager.get_config_section('post_processing')
    transcription = apply_word_replacements(transcription, post_processing.get('word_replacements'))
    if post_processing['remove_trailing_period'] and transcription.endswith('.'):
        transcription = transcription[:-1]
    if post_processing['add_trailing_space']:
        transcription += ' '
    if post_processing['remove_capitalization']:
        transcription = transcription.lower()

    return transcription

def transcribe(audio_data, local_model=None):
    """
    Transcribe audio using the configured engine (whisper.cpp GPU server or
    in-process faster-whisper).
    """
    if audio_data is None:
        return ''

    if resolve_engine() == 'whispercpp':
        transcription = transcribe_whispercpp(audio_data)
    else:  # 'faster-whisper'
        transcription = transcribe_local(audio_data, local_model)

    return post_process_transcription(transcription)

