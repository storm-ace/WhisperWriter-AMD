import io
import os
import numpy as np
import requests
import soundfile as sf
from faster_whisper import WhisperModel
from openai import OpenAI

from utils import ConfigManager


def resolve_engine():
    """
    Bepaal welke transcriptie-engine actief is.

    Nieuwe expliciete sleutel `model_options.engine` heeft voorrang; valt terug op de
    oude `use_api`-schakelaar voor achterwaartse compatibiliteit.
    Mogelijke waarden: 'faster-whisper' (CPU/CUDA, in-proces), 'whispercpp' (GPU via
    lokale whisper.cpp-server over HTTP), 'openai-api'.
    """
    engine = ConfigManager.get_config_value('model_options', 'engine')
    if engine:
        return engine
    return 'openai-api' if ConfigManager.get_config_value('model_options', 'use_api') else 'faster-whisper'

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

def transcribe_api(audio_data):
    """
    Transcribe an audio file using the OpenAI API.
    """
    model_options = ConfigManager.get_config_section('model_options')
    client = OpenAI(
        api_key=os.getenv('OPENAI_API_KEY') or None,
        base_url=model_options['api']['base_url'] or 'https://api.openai.com/v1'
    )

    # Convert numpy array to WAV file
    byte_io = io.BytesIO()
    sample_rate = ConfigManager.get_config_section('recording_options').get('sample_rate') or 16000
    sf.write(byte_io, audio_data, sample_rate, format='wav')
    byte_io.seek(0)

    response = client.audio.transcriptions.create(
        model=model_options['api']['model'],
        file=('audio.wav', byte_io, 'audio/wav'),
        language=model_options['common']['language'],
        prompt=model_options['common']['initial_prompt'],
        temperature=model_options['common']['temperature'],
    )
    return response.text

def transcribe_whispercpp(audio_data):
    """
    Transcribe via een lokale whisper.cpp `whisper-server` (GPU/Vulkan) over HTTP.

    Hergebruikt hetzelfde WAV-over-HTTP-patroon als de OpenAI-API-route. De int16-audio
    wordt direct als WAV verstuurd; de /32768.0-conversie van de in-proces-route is hier
    niet nodig (whisper.cpp leest de WAV zelf in).
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


def post_process_transcription(transcription):
    """
    Apply post-processing to the transcription.
    """
    transcription = transcription.strip()
    post_processing = ConfigManager.get_config_section('post_processing')
    if post_processing['remove_trailing_period'] and transcription.endswith('.'):
        transcription = transcription[:-1]
    if post_processing['add_trailing_space']:
        transcription += ' '
    if post_processing['remove_capitalization']:
        transcription = transcription.lower()

    return transcription

def transcribe(audio_data, local_model=None):
    """
    Transcribe audio date using the OpenAI API or a local model, depending on config.
    """
    if audio_data is None:
        return ''

    engine = resolve_engine()
    if engine == 'openai-api':
        transcription = transcribe_api(audio_data)
    elif engine == 'whispercpp':
        transcription = transcribe_whispercpp(audio_data)
    else:  # 'faster-whisper'
        transcription = transcribe_local(audio_data, local_model)

    return post_process_transcription(transcription)

