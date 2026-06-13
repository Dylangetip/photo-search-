import numpy as np
from PIL import Image, ImageDraw

from app import config, pipeline


def make_sheet(w=1700, h=1000) -> Image.Image:
    """Synthetic 4-up CAD sheet: near-white bg, grid midlines, a shape per quadrant."""
    img = Image.new("RGB", (w, h), (250, 250, 248))
    d = ImageDraw.Draw(img)
    d.line([(w // 2, 0), (w // 2, h)], fill=(200, 200, 200), width=2)
    d.line([(0, h // 2), (w, h // 2)], fill=(200, 200, 200), width=2)
    for cx, cy in [(w // 4, h // 4), (3 * w // 4, h // 4), (w // 4, 3 * h // 4), (3 * w // 4, 3 * h // 4)]:
        d.ellipse([cx - 80, cy - 80, cx + 80, cy + 80], outline=(120, 100, 60), width=6)
    return img


def make_beauty(w=900, h=900) -> Image.Image:
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([300, 300, 600, 600], outline=(180, 140, 80), width=14)
    return img


class TestSkuExtraction:
    def test_basic(self):
        assert pipeline.extract_sku("SKU123_views") == "SKU123"

    def test_dashed(self):
        assert pipeline.extract_sku("SW-2841_beauty") == "SW-2841"

    def test_no_match(self):
        assert pipeline.extract_sku("__nope") is None


class TestSheetDetection:
    def test_sheet_detected(self):
        assert pipeline.detect_sheet(make_sheet()) is True

    def test_beauty_not_sheet_wrong_ar(self):
        assert pipeline.detect_sheet(make_beauty()) is False

    def test_landscape_without_grid_not_sheet(self):
        img = Image.new("RGB", (1700, 1000), (255, 255, 255))
        ImageDraw.Draw(img).ellipse([100, 100, 1500, 900], outline=(0, 0, 0), width=4)
        # busy diagonal noise so no uniform midline exists
        d = ImageDraw.Draw(img)
        for i in range(0, 1700, 7):
            d.line([(i, 0), (i + 400, 1000)], fill=(i % 255, 80, 90), width=1)
        assert pipeline.detect_sheet(img) is False

    def test_split_quadrants(self):
        quads = pipeline.split_quadrants(make_sheet())
        assert len(quads) == 4
        # 6% inset per edge -> each quadrant is 88% of half-size
        w, h = quads[0].size
        assert abs(w - 1700 // 2 * 0.88) < 4
        assert abs(h - 1000 // 2 * 0.88) < 4


class TestPreprocess:
    def test_resize_longest_side(self):
        out = pipeline.composite_crop_resize(make_beauty().convert("RGBA"))
        assert max(out.size) <= config.VIEW_SIZE + 1

    def test_white_background(self):
        rgba = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        d = ImageDraw.Draw(rgba)
        d.ellipse([150, 150, 250, 250], fill=(200, 60, 60, 255))
        out = pipeline.composite_crop_resize(rgba)
        corner = out.getpixel((0, 0))
        assert corner == (255, 255, 255)


class TestEmbeddings:
    def test_normalized(self):
        embs = pipeline.embed_images([make_beauty()])
        assert embs.dtype == np.float32
        assert abs(np.linalg.norm(embs[0]) - 1.0) < 1e-4


class TestPoNumberSku:
    def test_po_regex_extracts_number(self, monkeypatch):
        monkeypatch.setattr(config, "SKU_REGEX", r"P\.?O\.?\s*#?\s*(\d+)")
        assert pipeline.extract_sku("Copy of P.O. #140892 - Josh Burtenshaw") == "140892"
        assert pipeline.extract_sku("Copy of P.O. 147591 - Skyler Townsend") == "147591"
        assert pipeline.extract_sku("random file") is None

    def test_default_regex_still_anchored(self):
        # default pattern has ^ so re.search keeps prefix behavior
        assert pipeline.extract_sku("SW-2841_views") == "SW-2841"


class TestExifOrientation:
    def test_orient_applies_exif(self):
        import io
        # landscape 100x50, tagged orientation 6 (rotate 90) -> should become 50x100
        img = Image.new("RGB", (100, 50), (200, 60, 60))
        exif = img.getexif(); exif[274] = 6
        buf = io.BytesIO(); img.save(buf, "JPEG", exif=exif)
        loaded = Image.open(io.BytesIO(buf.getvalue()))
        assert loaded.size == (100, 50)            # stored landscape
        assert pipeline.orient(loaded).size == (50, 100)  # honored rotation

    def test_orient_noop_without_exif(self):
        img = Image.new("RGB", (80, 40))
        assert pipeline.orient(img).size == (80, 40)


class TestQueryCropsContract:
    def test_returns_roles_and_preview(self):
        crops, preview = pipeline.query_crops(make_beauty())
        assert isinstance(preview, Image.Image)
        roles = [r for r, _ in crops]
        assert "full" in roles or "stone" in roles
