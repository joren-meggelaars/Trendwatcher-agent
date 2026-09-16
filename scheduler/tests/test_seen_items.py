from seen_items import SeenItemsCache


def test_new_cache_has_not_seen_anything(tmp_path):
    cache = SeenItemsCache.load(path=tmp_path / "seen.json")
    assert cache.has_seen(1, "https://example.com/a") is False


def test_mark_seen_is_reflected_immediately(tmp_path):
    cache = SeenItemsCache(path=tmp_path / "seen.json", data={})
    cache.mark_seen(1, "https://example.com/a")
    assert cache.has_seen(1, "https://example.com/a") is True
    assert cache.has_seen(2, "https://example.com/a") is False


def test_save_and_reload_roundtrips(tmp_path):
    path = tmp_path / "seen.json"
    cache = SeenItemsCache.load(path=path)
    cache.mark_seen(1, "https://example.com/a")
    cache.save()

    reloaded = SeenItemsCache.load(path=path)
    assert reloaded.has_seen(1, "https://example.com/a") is True
