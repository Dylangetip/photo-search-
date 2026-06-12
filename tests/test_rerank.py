import numpy as np

from app import classify, config, db, search
from app.main import app
from .test_api import client


def seed_two_rings():
    """Two SKUs at realistic photo->CAD cosines (~0.7), nearly tied."""
    db.upsert_sku("ROUND-RG")
    db.upsert_sku("OVAL-YG")
    z = np.zeros(4, dtype=np.float32)
    a = z.copy(); a[0] = 0.75; a[1] = np.sqrt(1 - 0.75 ** 2)   # cos 0.75 to q
    b = z.copy(); b[0] = 0.73; b[2] = np.sqrt(1 - 0.73 ** 2)   # cos 0.73 to q
    db.add_view("ROUND-RG", "r.png", "single", b)   # slightly worse cosine
    db.add_view("OVAL-YG", "o.png", "single", a)    # slightly better cosine
    db.set_tags("ROUND-RG", {"metal_color": "rose_gold", "center_stone_shape": "round",
                             "setting_type": "solitaire"}, "done")
    db.set_tags("OVAL-YG", {"metal_color": "yellow_gold", "center_stone_shape": "oval",
                            "setting_type": "halo"}, "done")
    q = z.copy(); q[0] = 1.0
    return q


class TestAttributeAgreement:
    def test_full_match(self):
        t = {"metal_color": "rose_gold", "center_stone_shape": "round", "setting_type": "solitaire"}
        assert search.attribute_agreement(t, t) == 1.0

    def test_partial_match(self):
        q = {"metal_color": "rose_gold", "center_stone_shape": "round", "setting_type": "solitaire"}
        c = {"metal_color": "rose_gold", "center_stone_shape": "oval", "setting_type": "solitaire"}
        agree = search.attribute_agreement(q, c)
        assert 0 < agree < 1

    def test_uninformative_fields_skipped(self):
        q = {"metal_color": "other", "center_stone_shape": "unclear", "setting_type": None}
        assert search.attribute_agreement(q, {"metal_color": "rose_gold"}) is None

    def test_empty(self):
        assert search.attribute_agreement({}, {"metal_color": "rose_gold"}) is None


class TestRerank:
    def test_attributes_flip_close_cosine(self):
        """A near-tie on cosine is decided by attribute agreement — the messy-photo case."""
        q = seed_two_rings()
        results = search.search_by_embedding(q, {}, top_k=36)
        assert results[0]["sku"] == "OVAL-YG"  # raw cosine winner

        query_tags = {"metal_color": "rose_gold", "center_stone_shape": "round",
                      "setting_type": "solitaire"}
        reranked = search.rerank_with_query_tags(results, query_tags,
                                                 config.QUERY_RERANK_WEIGHT)
        assert reranked[0]["sku"] == "ROUND-RG"  # attributes flip it
        assert reranked[0]["attr_match"] == 1.0

    def test_bonus_only_never_lowers_scores(self):
        """A disagreeing zero-shot read must never bury a strong visual match."""
        q = seed_two_rings()
        results = search.search_by_embedding(q, {}, top_k=36)
        before = {r["sku"]: r["score"] for r in results}
        wrong_tags = {"metal_color": "white_gold", "center_stone_shape": "pear",
                      "setting_type": "bezel"}
        out = search.rerank_with_query_tags(results, wrong_tags, 0.25)
        for r in out:
            assert r["score"] >= before[r["sku"]]  # never penalized

    def test_untagged_skus_not_penalized(self):
        db.upsert_sku("NOTAGS")
        v = np.zeros(4, dtype=np.float32); v[0] = 1.0
        db.add_view("NOTAGS", "n.png", "single", v)
        results = search.search_by_embedding(v, {}, top_k=36)
        before = results[0]["score"]
        out = search.rerank_with_query_tags(results, {"center_stone_shape": "round"}, 0.35)
        assert out[0]["sku"] == "NOTAGS"
        assert out[0]["score"] == before  # cosine untouched


class TestLocalAttrs:
    def test_detect_returns_taxonomy_values_only(self):
        from app import local_attrs
        local_attrs._cache.clear()
        q = np.random.RandomState(1).randn(32).astype(np.float32)
        q /= np.linalg.norm(q)
        tags = local_attrs.detect_attributes(q)
        for field, value in tags.items():
            if field.startswith("_"):
                continue
            assert value in local_attrs.PROMPT_SETS[field]

    def test_uncertain_fields_omitted(self, monkeypatch):
        """With a high acceptance bar nothing should be detected from noise."""
        from app import local_attrs
        local_attrs._cache.clear()
        monkeypatch.setattr(local_attrs, "ACCEPT_PROB", 1.01)  # unreachable bar
        q = np.random.RandomState(2).randn(32).astype(np.float32)
        q /= np.linalg.norm(q)
        tags = local_attrs.detect_attributes(q)
        assert not [k for k in tags if not k.startswith("_")]


class TestMultiCropSearch:
    def test_2d_query_takes_best_crop(self, monkeypatch):
        monkeypatch.setattr(config, "CENTER_EMBEDDINGS", False)  # raw-cosine mechanic
        db.upsert_sku("A")
        v = np.zeros(4, dtype=np.float32); v[0] = 1.0
        db.add_view("A", "a.png", "single", v)
        bad = np.zeros(4, dtype=np.float32); bad[1] = 1.0
        results = search.search_by_embedding(np.stack([bad, v]), {})
        assert results[0]["score"] == 1.0  # max over crops, not first crop


class TestEndpointIsFullyLocal:
    def test_image_search_makes_zero_api_calls(self, monkeypatch):
        """The core requirement: image search must consume zero tokens."""
        from .test_api import setup_catalog
        setup_catalog()
        # Even with a key configured, no anthropic client may be constructed.
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(config, "CLASSIFY_ENABLED", True)
        import sys

        class Bomb:
            def __getattr__(self, name):
                raise AssertionError("anthropic API touched during image search!")
        monkeypatch.setitem(sys.modules, "anthropic", Bomb())

        import io

        from .test_pipeline import make_beauty
        buf = io.BytesIO(); make_beauty().save(buf, format="PNG")
        with client() as c:
            r = c.post("/api/search/image",
                       files={"file": ("q.png", buf.getvalue(), "image/png")})
        assert r.status_code == 200
        body = r.json()
        assert body["usage"] is None
        assert len(body["results"]) > 0
        with db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) c FROM api_usage").fetchone()["c"] == 0

    def test_local_tags_boost_matching_sku(self, monkeypatch):
        from app import local_attrs

        from .test_api import setup_catalog
        setup_catalog()
        monkeypatch.setattr(local_attrs, "detect_attributes",
                            lambda vec: {"metal_color": "yellow_gold",
                                         "center_stone_shape": "marquise",
                                         "setting_type": "solitaire"})
        import io

        from .test_pipeline import make_beauty
        buf = io.BytesIO(); make_beauty().save(buf, format="PNG")
        with client() as c:
            r = c.post("/api/search/image",
                       files={"file": ("q.png", buf.getvalue(), "image/png")})
        body = r.json()
        assert body["query_tags"]["center_stone_shape"] == "marquise"
        # The query is the exact image indexed for SKU456, so the visual match
        # stays #1 (bonus-only rerank never buries an exact match) — but
        # SKU123 (tagged marquise/yellow_gold/solitaire) must carry the boost.
        assert body["results"][0]["sku"] == "SKU456"
        sku123 = next(r for r in body["results"] if r["sku"] == "SKU123")
        assert sku123["attr_match"] == 1.0
        assert sku123["score"] > 0  # boosted score present

    def test_detection_failure_falls_back_to_clip(self, monkeypatch):
        from app import local_attrs

        from .test_api import setup_catalog
        setup_catalog()

        def boom(vec):
            raise RuntimeError("detector exploded")
        monkeypatch.setattr(local_attrs, "detect_attributes", boom)
        import io

        from .test_pipeline import make_beauty
        buf = io.BytesIO(); make_beauty().save(buf, format="PNG")
        with client() as c:
            r = c.post("/api/search/image",
                       files={"file": ("q.png", buf.getvalue(), "image/png")})
        assert r.status_code == 200
        assert r.json()["query_tags"] is None
        assert len(r.json()["results"]) > 0


class TestUsageTracking:
    def test_sku_classify_usage_logged_and_summed(self):
        db.log_api_usage("sku_classify", "SKU1", "claude-sonnet-4-6",
                         {"input_tokens": 4000, "output_tokens": 300})
        db.log_api_usage("sku_classify", "SKU2", "claude-sonnet-4-6",
                         {"input_tokens": 5000, "output_tokens": 400})
        u = db.usage_stats()
        assert u["total"]["calls"] == 2
        assert u["total"]["input_tokens"] == 9000
        assert u["by_kind"]["sku_classify"]["output_tokens"] == 700
        # 9000*3/1e6 + 700*15/1e6 = 0.027 + 0.0105
        assert abs(u["total"]["estimated_cost_usd"] - 0.0375) < 1e-6
