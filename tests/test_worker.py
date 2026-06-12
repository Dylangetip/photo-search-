import json

from PIL import Image

from app import config, db, worker
from .test_pipeline import make_beauty, make_sheet


def drop(img: Image.Image, name: str, sub: str = "") -> None:
    folder = config.INBOX_DIR / sub if sub else config.INBOX_DIR
    folder.mkdir(parents=True, exist_ok=True)
    img.save(folder / name)


def ingest_all():
    """Two stable-scan passes (size must be unchanged across polls), then process."""
    pending = {}
    worker._stable_scan(pending)          # first sighting: records sizes
    for path, forced in worker._stable_scan(pending):
        worker.process_file(path, forced)


class TestIngestion:
    def test_sheet_ingest(self):
        """Acceptance #1: 4-up sheet -> 4 views, 4 embeddings, inbox empty."""
        drop(make_sheet(), "SKU123_views.jpg")
        ingest_all()
        views = db.sku_views("SKU123")
        assert len(views) == 4
        assert all(v["source_type"] == "sheet_quadrant" for v in views)
        for v in views:
            assert (config.DATA_DIR / v["file_path"]).exists()
        assert not list(config.INBOX_DIR.glob("*.jpg"))
        assert (config.LIBRARY_DIR / "SKU123" / "originals" / "SKU123_views.jpg").exists()

    def test_beauty_adds_view_no_duplicate(self):
        """Acceptance #2: Type B render for same SKU adds 1 view, no dup on re-drop."""
        drop(make_sheet(), "SKU123_views.jpg")
        ingest_all()
        drop(make_beauty(), "SKU123_beauty.png")
        ingest_all()
        assert len(db.sku_views("SKU123")) == 5
        # idempotency: re-drop the same beauty render
        drop(make_beauty(), "SKU123_beauty.png")
        ingest_all()
        assert len(db.sku_views("SKU123")) == 5

    def test_no_sku_goes_to_failed(self):
        """Acceptance #3: no SKU match -> failed/ with a .log.txt."""
        drop(make_beauty(), "__bad name__.png")
        ingest_all()
        failed = list(config.FAILED_DIR.glob("*.png"))
        assert len(failed) == 1
        log = failed[0].with_name(failed[0].name + ".log.txt")
        assert log.exists()
        assert "SKU" in log.read_text()

    def test_forced_type_folders(self):
        drop(make_beauty(900, 900), "SKU777_x.png", sub="sheets")  # forced sheet despite AR
        ingest_all()
        assert len(db.sku_views("SKU777")) == 4

    def test_unstable_file_waits(self):
        drop(make_beauty(), "SKU555_a.png")
        pending = {}
        assert worker._stable_scan(pending) == []          # first pass: not ready
        assert len(worker._stable_scan(pending)) == 1      # unchanged size: ready


class TestClassifyParsing:
    def test_strip_code_fences(self):
        from app.classify import parse_tags
        raw = '```json\n{"metal_color": "yellow_gold", "overall_confidence": 0.9}\n```'
        assert parse_tags(raw)["metal_color"] == "yellow_gold"

    def test_prose_wrapped(self):
        from app.classify import parse_tags
        raw = 'Here is the JSON:\n{"metal_color": "rose_gold"}\nHope that helps!'
        assert parse_tags(raw)["metal_color"] == "rose_gold"

    def test_classify_pending_without_key_is_noop(self):
        db.upsert_sku("SKUX")
        assert worker.classify_pending() == 0
        assert db.get_sku("SKUX")["tags_status"] == "pending"


class TestPricing:
    def test_left_join_semantics(self):
        db.upsert_sku("SKU1")
        assert db.set_pricing("SKU1", 1234.0, "Aurelia") is True
        assert db.set_pricing("UNKNOWN", 1.0, None) is False
        row = db.get_sku("SKU1")
        assert row["price"] == 1234.0 and row["name"] == "Aurelia"


class TestTags:
    def test_set_and_filter(self):
        db.upsert_sku("SKU9")
        db.set_tags("SKU9", {"metal_color": "yellow_gold"}, "done")
        row = db.get_sku("SKU9")
        assert json.loads(row["tags_json"])["metal_color"] == "yellow_gold"
        assert row["tags_status"] == "done"
        assert "SKU9" not in db.pending_skus()
