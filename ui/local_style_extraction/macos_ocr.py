"""Offline style-reference OCR using the macOS Vision framework."""
from pathlib import Path

from PIL import Image


def ocr_words(path, *, max_words=12):
    import Vision
    from Foundation import NSData
    import objc

    source = Path(path).resolve()
    with Image.open(source) as image:
        width, height = image.size
    with objc.autorelease_pool():
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setRecognitionLanguages_(["en-US"])
        request.setUsesLanguageCorrection_(False)
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
            NSData.dataWithContentsOfFile_(str(source)), None
        )
        success, error = handler.performRequests_error_([request], None)
        if not success:
            return {"ok": False, "reason": str(error), "words": [], "method": "macos_vision"}
        words = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            text = str(candidate.string()).strip()
            letters = sum(ch.isalnum() for ch in text)
            if letters < 2:
                continue
            box = observation.boundingBox()
            left = max(0, round(box.origin.x * width))
            top = max(0, round((1 - box.origin.y - box.size.height) * height))
            box_width = min(width - left, round(box.size.width * width))
            box_height = min(height - top, round(box.size.height * height))
            if box_width < 4 or box_height < 6:
                continue
            words.append({"text": text, "bbox": [left, top, box_width, box_height],
                          "conf": float(candidate.confidence()) * 100, "letters": letters})
            if len(words) >= max_words:
                break
    return {"ok": True, "reason": "", "words": words, "n_words": len(words),
            "letter_count": sum(word["letters"] for word in words),
            "image": str(source), "method": "macos_vision"}
