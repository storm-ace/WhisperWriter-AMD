"""
Microphone discovery and selection.

PortAudio lists every microphone once per Windows audio API (MME, DirectSound, WASAPI,
WDM-KS), MME cuts names off at 31 characters, and WASAPI refuses the 16 kHz mono stream
Whisper wants. So the app records through one API (MME on Windows: it resamples for us),
shows each microphone once with its full name, and stores the selection as
"<device name>, <host API>" — a string sounddevice itself understands.
"""

from dataclasses import dataclass

import sounddevice as sd

_SKIP = ('sound mapper', 'geluidstoewijzing', 'primary sound capture', 'primair stuurprogramma')


@dataclass
class InputDevice:
    label: str      # full, human-readable name
    value: str      # what is stored in config (recording_options.sound_device)
    index: int


def _hostapi_names():
    return [api['name'] for api in sd.query_hostapis()]


def _recording_api():
    names = _hostapi_names()
    if 'MME' in names:
        return 'MME'
    return names[sd.default.hostapi] if names else ''


def _full_names():
    """Full device names from WASAPI (or any API) to repair MME's truncated ones."""
    return [d['name'] for d in sd.query_devices() if d['max_input_channels'] > 0]


def list_inputs():
    """Microphones the app can record from, once each, in system order."""
    try:
        apis = _hostapi_names()
        api = _recording_api()
        full = _full_names()
        devices, seen = [], set()
        for index, dev in enumerate(sd.query_devices()):
            if dev['max_input_channels'] <= 0 or apis[dev['hostapi']] != api:
                continue
            name = dev['name']
            if any(s in name.lower() for s in _SKIP) or name in seen:
                continue
            seen.add(name)
            label = next((f for f in full if f.startswith(name) and len(f) > len(name)), name)
            devices.append(InputDevice(label, f'{name}, {api}', index))
        return devices
    except Exception:
        return []


def default_input():
    """The Windows default microphone as an InputDevice, or None."""
    try:
        index = sd.default.device[0]
        if index is None or index < 0:
            return None
        for dev in list_inputs():
            if dev.index == index:
                return dev
        info = sd.query_devices(index)
        return InputDevice(info['name'], info['name'], index)
    except Exception:
        return None


def resolve(value):
    """Config value -> PortAudio device index (None = system default / not found)."""
    if value in (None, ''):
        return None
    if isinstance(value, int) or str(value).isdigit():
        return int(value)
    inputs = list_inputs()
    for dev in inputs:                       # exact stored value or same name
        if value == dev.value or value == dev.label or dev.value.startswith(f'{value}, '):
            return dev.index
    for dev in inputs:                       # older configs: any substring of the name
        if str(value).lower() in dev.label.lower():
            return dev.index
    return None


def describe(value):
    """Human-readable name of the microphone a config value selects."""
    if value in (None, ''):
        dev = default_input()
        return f'{dev.label} (Windows default)' if dev else 'Windows default microphone'
    index = resolve(value)
    if index is None:
        return f'{value} (not connected — using the default)'
    for dev in list_inputs():
        if dev.index == index:
            return dev.label
    return str(value)


def short_name(value):
    """Compact microphone name for the recorder pill: 'Hoofdtelefoon (JBL TOUR ONE M2)' -> 'JBL TOUR ONE M2'."""
    index = resolve(value)
    dev = next((d for d in list_inputs() if d.index == index), None) if index is not None else default_input()
    if dev is None:
        return 'default mic'
    label = dev.label
    if '(' in label and label.rstrip().endswith(')'):
        label = label[label.index('(') + 1:-1]
    return label if len(label) <= 28 else label[:27] + '…'


def refresh():
    """Re-scan devices (plugged in / removed). Only call while no stream is open."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        pass
