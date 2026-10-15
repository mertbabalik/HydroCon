"""Growth stage classification on the Pi.

Images come from either:
  * the growth node's MJPEG stream (http://<esp32-cam>/stream), one frame at a time
  * base64 JPEGs published on nft/growth_node/image

The model is the full ResNet-18 saved by ml/train_growth_stage_model.py
(torch.save(model)), with class names from class_names.json. PyTorch is
optional: without it the controller keeps using the configured stage.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.request
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

IMAGE_SIZE = 128  # must match the training script
MEAN = [0.485, 0.456, 0.406]
STD = [0.229, 0.224, 0.225]


def grab_mjpeg_frame(url: str, timeout: float = 10.0, max_bytes: int = 2_000_000) -> bytes:
    """Read one JPEG frame from an MJPEG stream and close the connection.

    The ESP32 stream handler blocks its main loop while a client is connected,
    so we disconnect as soon as we have a full frame.
    """
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        buf = b""
        while len(buf) < max_bytes:
            chunk = resp.read(4096)
            if not chunk:
                break
            buf += chunk
            start = buf.find(b"\xff\xd8")
            if start != -1:
                end = buf.find(b"\xff\xd9", start + 2)
                if end != -1:
                    return buf[start:end + 2]
    raise IOError(f"No complete JPEG frame received from {url}")


class GrowthClassifier:
    def __init__(self, model_path: str, class_names_path: str):
        import torch  # noqa: F401  (raises ImportError if not installed)
        from torchvision import transforms

        self.torch = torch
        with open(class_names_path) as f:
            self.class_names: List[str] = json.load(f)
        self.model = torch.load(model_path, map_location="cpu", weights_only=False)
        self.model.eval()
        self.transform = transforms.Compose([
            transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(MEAN, STD),
        ])
        log.info("Loaded growth model with classes %s", self.class_names)

    def predict(self, jpeg: bytes) -> Tuple[str, float]:
        from PIL import Image

        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        x = self.transform(img).unsqueeze(0)
        with self.torch.no_grad():
            probs = self.torch.softmax(self.model(x), dim=1)[0]
        idx = int(probs.argmax())
        return self.class_names[idx], float(probs[idx])


def load_classifier(model_path: str, class_names_path: str) -> Optional[GrowthClassifier]:
    try:
        return GrowthClassifier(model_path, class_names_path)
    except ImportError:
        log.warning("PyTorch not installed: growth stage classification disabled")
    except FileNotFoundError as e:
        log.warning("Growth model not found (%s): classification disabled", e.filename)
    except Exception:
        log.exception("Could not load growth model: classification disabled")
    return None
