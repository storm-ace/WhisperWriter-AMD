import time
import traceback
import numpy as np
import sounddevice as sd
import tempfile
import wave
import webrtcvad
from PySide6.QtCore import QThread, QMutex, Signal
from collections import deque
from threading import Event

from transcription import transcribe_detailed
from utils import ConfigManager


class ResultThread(QThread):
    """
    A thread class for handling audio recording, transcription, and result processing.

    This class manages the entire process of:
    1. Recording audio from the microphone
    2. Detecting speech and silence
    3. Saving the recorded audio as numpy array
    4. Transcribing the audio
    5. Emitting the transcription result

    Signals:
        statusSignal: Emits the current status of the thread (e.g., 'recording', 'transcribing', 'idle')
        resultSignal: Emits a dict: text, raw, corrected, duration_s, latency_s
        levelSignal: Emits the microphone input level (0..1) while recording
    """

    statusSignal = Signal(str)
    resultSignal = Signal(dict)
    levelSignal = Signal(float)

    def __init__(self, local_model=None):
        """
        Initialize the ResultThread.

        :param local_model: Local transcription model (if applicable)
        """
        super().__init__()
        self.local_model = local_model
        self.is_recording = False
        self.is_running = True
        self.sample_rate = None
        self.mutex = QMutex()

    def stop_recording(self):
        """Stop the current recording session."""
        self.mutex.lock()
        self.is_recording = False
        self.mutex.unlock()

    def stop(self):
        """Stop the entire thread execution."""
        self.mutex.lock()
        self.is_running = False
        self.mutex.unlock()
        self.statusSignal.emit('idle')
        self.wait()

    def run(self):
        """Main execution method for the thread."""
        try:
            if not self.is_running:
                return

            self.mutex.lock()
            self.is_recording = True
            self.mutex.unlock()

            self.statusSignal.emit('recording')
            ConfigManager.console_print('Recording...')
            audio_data = self._record_audio()

            if not self.is_running:
                return

            if audio_data is None:
                # Report the discard so continuous mode keeps listening.
                self.statusSignal.emit('idle')
                self.resultSignal.emit({'text': '', 'discarded': True})
                return

            self.statusSignal.emit('transcribing')
            ConfigManager.console_print('Transcribing...')

            # Time the transcription process
            start_time = time.time()
            result = transcribe_detailed(audio_data, self.local_model)
            transcription_time = time.time() - start_time
            result['latency_s'] = transcription_time
            result['duration_s'] = len(audio_data) / self.sample_rate

            ConfigManager.console_print(f'Transcription completed in {transcription_time:.2f} seconds. Post-processed line: {result["text"]}')

            if not self.is_running:
                return

            self.statusSignal.emit('idle')
            self.resultSignal.emit(result)

        except Exception as e:
            traceback.print_exc()
            self.statusSignal.emit('error')
            self.resultSignal.emit({'text': '', 'error': str(e)})
        finally:
            self.stop_recording()

    def _record_audio(self):
        """
        Record audio from the microphone until the recording mode says stop.

        Speech detection is deliberately strict, because Whisper invents text ("Thank you.")
        when it is fed noise or silence:
        - a frame only counts as speech when WebRTC VAD says so AND it is clearly louder
          than the measured background noise floor;
        - speech only "starts" after several speech frames within a short window, so a
          click or a cough does not trigger a recording;
        - before speech starts only a short pre-roll is kept, and trailing silence is
          trimmed, so Whisper never sees long stretches of silence;
        - a recording with less than `min_speech_duration` ms of speech is discarded.

        :return: numpy int16 array, or None when there was no (or too little) speech
        """
        recording_options = ConfigManager.get_config_section('recording_options')
        self.sample_rate = recording_options.get('sample_rate') or 16000
        frame_ms = 30  # WebRTC VAD accepts 10/20/30 ms frames
        frame_size = int(self.sample_rate * frame_ms / 1000)
        silence_frames = int((recording_options.get('silence_duration') or 1200) / frame_ms)
        min_speech_frames = max(1, int((recording_options.get('min_speech_duration') or 300) / frame_ms))
        aggressiveness = recording_options.get('vad_aggressiveness')
        aggressiveness = 3 if aggressiveness is None else max(0, min(3, int(aggressiveness)))

        recording_mode = recording_options.get('recording_mode') or 'continuous'
        auto_stop = recording_mode in ('voice_activity_detection', 'continuous')

        vad = webrtcvad.Vad(aggressiveness)
        onset_window = deque(maxlen=10)          # last 300 ms of speech/non-speech flags
        onset_needed = 6                         # >= 180 ms of speech in that window
        pre_roll = deque(maxlen=int(400 / frame_ms))
        tail_keep = int(300 / frame_ms)          # silence kept after the last speech frame

        # Skip the first 150 ms so the hotkey click is not mistaken for speech.
        skip_frames = int(150 / frame_ms)
        noise_floor = None
        min_abs_rms = 80.0                       # ~ -52 dBFS: below this is never speech

        speech_started = not auto_stop           # manual modes record from the first frame
        speech_frames = 0
        silent_run = 0
        frames = []                              # recorded frames (after onset for auto modes)
        last_speech_index = -1

        audio_buffer = deque(maxlen=self.sample_rate * 2)   # 2 s of headroom
        data_ready = Event()
        level_tick = 0

        def audio_callback(indata, n, time_info, status):
            if status:
                ConfigManager.console_print(f"Audio callback status: {status}")
            audio_buffer.extend(indata[:, 0])
            data_ready.set()

        with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype='int16',
                            blocksize=frame_size, device=recording_options.get('sound_device'),
                            callback=audio_callback):
            while self.is_running and self.is_recording:
                data_ready.wait(0.5)
                data_ready.clear()

                while len(audio_buffer) >= frame_size and self.is_running and self.is_recording:
                    frame = np.array([audio_buffer.popleft() for _ in range(frame_size)], dtype=np.int16)
                    rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))

                    level_tick += 1
                    if level_tick % 2 == 0:
                        self.levelSignal.emit(min(1.0, rms / 32768.0 * 8))

                    if skip_frames > 0:
                        skip_frames -= 1
                        if speech_started:
                            frames.append(frame)
                        continue

                    floor = noise_floor if noise_floor is not None else min_abs_rms
                    # Strict to start speech, more lenient once it is going (soft syllables,
                    # trailing words) so sentences are not chopped up.
                    if speech_started and auto_stop:
                        loud_enough = rms > max(min_abs_rms * 0.6, floor * 1.6)
                    else:
                        loud_enough = rms > max(min_abs_rms, floor * 2.5)
                    is_speech = loud_enough and vad.is_speech(frame.tobytes(), self.sample_rate)
                    if not is_speech:
                        # Background level: follows quiet frames quickly, loud ones slowly.
                        rate = 0.2 if rms < floor else 0.02
                        noise_floor = floor * (1 - rate) + rms * rate

                    if not speech_started:
                        pre_roll.append(frame)
                        onset_window.append(is_speech)
                        if sum(onset_window) >= onset_needed:
                            speech_started = True
                            ConfigManager.console_print('Speech detected.')
                            frames.extend(pre_roll)
                            speech_frames = sum(onset_window)
                            last_speech_index = len(frames) - 1
                            silent_run = 0
                        continue

                    frames.append(frame)
                    if is_speech:
                        speech_frames += 1
                        silent_run = 0
                        last_speech_index = len(frames) - 1
                    else:
                        silent_run += 1
                        if auto_stop and silent_run > silence_frames:
                            break
                else:
                    continue
                break

        if auto_stop and last_speech_index >= 0:
            frames = frames[:last_speech_index + 1 + tail_keep]

        audio_data = np.concatenate(frames) if frames else np.zeros(0, dtype=np.int16)
        duration = len(audio_data) / self.sample_rate
        ConfigManager.console_print(
            f'Recording finished. Duration: {duration:.2f} s, speech: {speech_frames * frame_ms} ms')

        min_duration_ms = recording_options.get('min_duration') or 100
        if duration * 1000 < min_duration_ms or speech_frames < min_speech_frames:
            ConfigManager.console_print('Discarded: no or too little speech.')
            return None

        return audio_data
