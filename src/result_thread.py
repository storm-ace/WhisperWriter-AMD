import queue
import threading
import time
import traceback
from collections import deque
from enum import Enum

import numpy as np
import sounddevice as sd
import webrtcvad
from PySide6.QtCore import QThread, Signal

import audio_devices
from transcription import TranscriptionResult, transcribe_detailed
from utils import ConfigManager


class Status(str, Enum):
    RECORDING = 'recording'
    TRANSCRIBING = 'transcribing'
    IDLE = 'idle'
    ERROR = 'error'


class SpeechSegmenter:
    """
    Turns a stream of 30 ms frames into speech segments.

    Detection is deliberately strict, because Whisper invents text ("Thank you.") when it
    is fed noise or silence:
    - a frame only counts as speech when WebRTC VAD says so AND it is clearly louder than
      the measured background noise floor (stricter to start speech, more lenient while
      speech is going so soft syllables don't chop sentences);
    - speech only "starts" after several speech frames within a short window, so a click
      or a cough does not open a segment;
    - before speech starts only a short pre-roll is kept, and trailing silence is trimmed,
      so Whisper never sees long stretches of silence;
    - a segment with less than `min_speech_ms` of speech is discarded.

    With `auto_stop` (continuous / voice-detect modes) a segment ends after `silence_ms` of
    silence. Without it (toggle / hold) everything is one segment until `flush()`.
    """

    FRAME_MS = 30
    MIN_ABS_RMS = 80.0          # ~ -52 dBFS: below this is never speech

    def __init__(self, sample_rate, silence_ms, min_speech_ms, aggressiveness, auto_stop):
        self.sample_rate = sample_rate
        self.frame_size = int(sample_rate * self.FRAME_MS / 1000)
        self.silence_frames = int(silence_ms / self.FRAME_MS)
        self.min_speech_frames = max(1, int(min_speech_ms / self.FRAME_MS))
        self.auto_stop = auto_stop
        self.vad = webrtcvad.Vad(aggressiveness)
        self.onset = deque(maxlen=10)            # last 300 ms of speech flags
        self.onset_needed = 6                    # >= 180 ms of speech in that window
        self.pre_roll = deque(maxlen=int(400 / self.FRAME_MS))
        self.tail_keep = int(300 / self.FRAME_MS)
        self.skip_frames = int(150 / self.FRAME_MS)   # the hotkey click
        self.noise_floor = None
        self._reset_segment()

    def _reset_segment(self):
        self.started = not self.auto_stop
        self.frames = []
        self.speech_frames = 0
        self.silent_run = 0
        self.last_speech = -1
        self.onset.clear()
        self.pre_roll.clear()

    def _is_speech(self, frame, rms):
        floor = self.noise_floor if self.noise_floor is not None else self.MIN_ABS_RMS
        if self.started and self.auto_stop:
            loud_enough = rms > max(self.MIN_ABS_RMS * 0.6, floor * 1.6)
        else:
            loud_enough = rms > max(self.MIN_ABS_RMS, floor * 2.5)
        speech = loud_enough and self.vad.is_speech(frame.tobytes(), self.sample_rate)
        if not speech:
            # Background level: follows quiet frames quickly, loud ones slowly.
            rate = 0.2 if rms < floor else 0.02
            self.noise_floor = floor * (1 - rate) + rms * rate
        return speech

    def process(self, frame, rms):
        """Feed one frame. Returns (segment_ended, audio_or_None)."""
        if self.skip_frames > 0:
            self.skip_frames -= 1
            if self.started:
                self.frames.append(frame)
            return False, None

        speech = self._is_speech(frame, rms)
        if not self.started:
            self.pre_roll.append(frame)
            self.onset.append(speech)
            if sum(self.onset) >= self.onset_needed:
                self.started = True
                ConfigManager.console_print('Speech detected.')
                self.frames.extend(self.pre_roll)
                self.speech_frames = sum(self.onset)
                self.last_speech = len(self.frames) - 1
            return False, None

        self.frames.append(frame)
        if speech:
            self.speech_frames += 1
            self.silent_run = 0
            self.last_speech = len(self.frames) - 1
        else:
            self.silent_run += 1
            if self.auto_stop and self.silent_run > self.silence_frames:
                return True, self.flush()
        return False, None

    def flush(self):
        """End the current segment. Returns its audio, or None when it held too little speech."""
        frames = self.frames
        if self.auto_stop and self.last_speech >= 0:
            frames = frames[:self.last_speech + 1 + self.tail_keep]
        speech_frames = self.speech_frames
        self._reset_segment()
        audio = np.concatenate(frames) if frames else np.zeros(0, dtype=np.int16)
        ConfigManager.console_print(f'Segment: {len(audio) / self.sample_rate:.2f} s, '
                                    f'speech {speech_frames * self.FRAME_MS} ms')
        if speech_frames < self.min_speech_frames:
            ConfigManager.console_print('Discarded: no or too little speech.')
            return None
        return audio


class ResultThread(QThread):
    """
    Records from the microphone and transcribes what was said.

    The microphone stays open for the whole recording. In continuous mode every finished
    speech segment is handed to a transcription worker while recording simply goes on, so
    nothing said during transcription is lost. Other modes record one segment, then
    transcribe it.

    `stop_recording()` ends the recording but still transcribes what was said; `cancel()`
    drops everything. Neither blocks the caller.

    Signals:
        statusSignal: Status value (recording, transcribing, idle, error)
        resultSignal: TranscriptionResult, once per transcribed segment
        levelSignal: microphone input level (0..1) for the waveform
    """

    statusSignal = Signal(str)
    resultSignal = Signal(object)
    levelSignal = Signal(float)

    def __init__(self, local_model=None, continuous=False):
        super().__init__()
        self.local_model = local_model
        self.continuous = continuous
        self.sample_rate = 16000
        self._recording = threading.Event()
        self._recording.set()
        self._cancelled = threading.Event()
        self._jobs = queue.Queue()

    @property
    def is_recording(self):
        return self._recording.is_set() and not self._cancelled.is_set()

    def stop_recording(self):
        """Stop listening; whatever was said so far is still transcribed."""
        self._recording.clear()

    def cancel(self):
        """Stop listening and discard anything not yet typed."""
        self._cancelled.set()
        self._recording.clear()

    # ------------------------------------------------------------------ thread body
    def run(self):
        worker = threading.Thread(target=self._transcribe_worker, daemon=True)
        worker.start()
        try:
            self.statusSignal.emit(Status.RECORDING)
            ConfigManager.console_print('Recording...')
            final_audio = self._record()
            if final_audio is not None and not self._cancelled.is_set():
                self.statusSignal.emit(Status.TRANSCRIBING)
                self._jobs.put(final_audio)
        except Exception as e:
            traceback.print_exc()
            self.statusSignal.emit(Status.ERROR)
            self.resultSignal.emit(TranscriptionResult(error=str(e)))
        finally:
            self._recording.clear()
            self._jobs.put(None)
            worker.join()
            self.statusSignal.emit(Status.IDLE)

    def _record(self):
        """Run the microphone until stopped. Returns the last segment to transcribe, if any."""
        opts = ConfigManager.get_config_section('recording_options')
        self.sample_rate = opts.get('sample_rate') or 16000
        mode = opts.get('recording_mode') or 'continuous'
        aggressiveness = opts.get('vad_aggressiveness')
        segmenter = SpeechSegmenter(
            self.sample_rate,
            silence_ms=opts.get('silence_duration') or 1200,
            min_speech_ms=opts.get('min_speech_duration') or 300,
            aggressiveness=3 if aggressiveness is None else max(0, min(3, int(aggressiveness))),
            auto_stop=mode in ('voice_activity_detection', 'continuous'))
        frame_size = segmenter.frame_size

        buffer = deque(maxlen=self.sample_rate * 2)   # 2 s of headroom
        data_ready = threading.Event()
        level_tick = 0

        def audio_callback(indata, frames, time_info, status):
            if status:
                ConfigManager.console_print(f'Audio callback status: {status}')
            buffer.extend(indata[:, 0])
            data_ready.set()

        with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype='int16',
                            blocksize=frame_size, device=audio_devices.resolve(opts.get('sound_device')),
                            callback=audio_callback):
            while self.is_recording:
                data_ready.wait(0.5)
                data_ready.clear()
                while len(buffer) >= frame_size and self.is_recording:
                    frame = np.array([buffer.popleft() for _ in range(frame_size)], dtype=np.int16)
                    rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
                    level_tick += 1
                    if level_tick % 2 == 0:
                        self.levelSignal.emit(min(1.0, rms / 32768.0 * 8))

                    ended, audio = segmenter.process(frame, rms)
                    if ended and audio is not None:
                        if not self.continuous:
                            return audio          # voice-detect: one segment, then stop
                        self._jobs.put(audio)     # continuous: transcribe while listening on

        if self._cancelled.is_set():
            return None
        return segmenter.flush()

    def _transcribe_worker(self):
        while True:
            audio = self._jobs.get()
            if audio is None:
                return
            if self._cancelled.is_set():
                continue
            try:
                start = time.time()
                result = transcribe_detailed(audio, self.local_model)
                result.latency_s = time.time() - start
                result.duration_s = len(audio) / self.sample_rate
                ConfigManager.console_print(
                    f'Transcription completed in {result.latency_s:.2f} seconds: {result.text}')
            except Exception as e:
                traceback.print_exc()
                result = TranscriptionResult(error=str(e))
                self._recording.clear()   # a broken engine would otherwise fail on every segment
            if not self._cancelled.is_set():
                self.resultSignal.emit(result)
