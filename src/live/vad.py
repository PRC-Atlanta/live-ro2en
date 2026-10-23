"""Silero VAD v5 (MIT) via onnxruntime: 512-sample frames at 16 kHz (32 ms), with the
64-sample context the model expects (mirrors silero-vad's OnnxWrapper)."""
from __future__ import annotations

import pathlib

import numpy as np

MODEL = pathlib.Path(__file__).resolve().parents[2] / "models" / "vad" / "silero_vad.onnx"
FRAME = 512
CONTEXT = 64


class SileroVAD:
    def __init__(self, path: pathlib.Path = MODEL):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(str(path), sess_options=opts,
                                         providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, CONTEXT), dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self.context, frame.reshape(1, FRAME).astype(np.float32)], axis=1)
        out, self.state = self.sess.run(None, {"input": x, "state": self.state,
                                               "sr": np.array(16000, dtype=np.int64)})
        self.context = x[:, -CONTEXT:]
        return float(out[0, 0])
