import sys
from warnings import warn

from django.conf import settings
from django.db.models.signals import post_save, post_delete


def use_framework_for_site_cache():
    """Patches sites app to use the caching framework instead of a dict."""
    # This patch has to exist because SITE_CACHE is normally a dict,
    # which is local only to the process. When running multiple
    # processes, a change to a Site will not be reflected across other
    # ones.
    from django.contrib.sites import models

    # Patch the SITE_CACHE
    site_cache = SiteCache()
    models.SITE_CACHE = DictCache(site_cache)

    # Patch the SiteManager class
    models.SiteManager.clear_cache = SiteManager_clear_cache

    # Hooks to update SiteCache
    post_save.connect(site_cache._site_changed_hook, sender=models.Site)
    post_delete.connect(site_cache._site_deleted_hook, sender=models.Site)


# Override SiteManager.clear_cache so it doesn't clobber SITE_CACHE
def SiteManager_clear_cache(self):
    """Clears the ``Site`` object cache."""
    models = sys.modules.get(self.__class__.__module__)
    models.SITE_CACHE.clear()


class SiteCache(object):
    """Wrapper for SITE_CACHE that assigns a key_prefix."""

    def __init__(self, cache=None, key_prefix=None):
        from django.core.cache import get_cache

        self._key_prefix = key_prefix

        if cache is None:
            cache_alias = getattr(settings, 'CACHE_SITES_ALIAS', 'default')
            cache = get_cache(cache_alias, KEY_PREFIX=self.key_prefix)
            self._warn_cache_backend(cache, cache_alias)
        self._cache = cache

    def _warn_cache_backend(self, cache, cache_alias):
        from django.core.cache.backends.dummy import DummyCache
        from django.core.cache.backends.db import DatabaseCache
        from django.core.cache.backends.filebased import FileBasedCache
        from django.core.cache.backends.locmem import LocMemCache

        if isinstance(cache, (LocMemCache, FileBasedCache)):
            warn(("'%s' cache is %s, which may cause stale caches." %
                  (cache_alias, type(cache).__name__)),
                 RuntimeWarning, stacklevel=3)
        elif isinstance(cache, (DatabaseCache, DummyCache)):
            warn(("'%s' is %s, causing extra database queries." %
                  (cache_alias, type(cache).__name__)),
                 RuntimeWarning, stacklevel=3)

    def _get_cache_key(self, key):
        return 'sites.%s.%s' % (self.key_prefix, key)

    def _clean_site(self, site):
        # Force site.id to be an int, not a SiteID object.
        site.id = int(site.id)
        return site

    @property
    def key_prefix(self):
        if self._key_prefix is None:
            return getattr(settings, 'CACHE_SITES_KEY_PREFIX', '')
        return self._key_prefix

    def get(self, key, *args, **kwargs):
        return self._cache.get(key=self._get_cache_key(key), *args, **kwargs)

    def set(self, key, value, *args, **kwargs):
        self._cache.set(key=self._get_cache_key(key),
                        value=self._clean_site(value),
                        *args, **kwargs)

    def delete(self, key, *args, **kwargs):
        self._cache.delete(key=self._get_cache_key(key), *args, **kwargs)

    def __contains__(self, key, *args, **kwargs):
        return self._cache.__contains__(key=self._get_cache_key(key),
                                        *args, **kwargs)

    def clear(self, *args, **kwargs):
        self._cache.clear(*args, **kwargs)

    def _site_changed_hook(self, sender, instance, raw, *args, **kwargs):
        if raw:
            return
        self.set(key=instance.pk, value=instance)

    def _site_deleted_hook(self, sender, instance, *args, **kwargs):
        self.delete(key=instance.pk)


class DictCache(object):
    """Add dictionary protocol to django.core.cache.backends.BaseCache."""

    def __init__(self, cache):
        self._cache = cache

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __getitem__(self, key):
        """x.__getitem__(y) <==> x[y]"""
        hash(key)               # Raise TypeError if unhashable
        result = self._cache.get(key=key)
        if result is None:
            raise KeyError(key)
        return result

    def __setitem__(self, key, value):
        """x.__setitem__(i, y) <==> x[i]=y"""
        hash(key)               # Raise TypeError if unhashable
        self._cache.set(key=key, value=value)

    def __delitem__(self, key):
        """x.__delitem__(y) <==> del x[y]"""
        hash(key)               # Raise TypeError if unhashable
        self._cache.delete(key=key)

    def __contains__(self, item):
        """D.__contains__(k) -> True if D has a key k, else False"""
        hash(item)              # Raise TypeError if unhashable
        return self._cache.__contains__(key=item)

    def clear(self):
        """D.clear() -> None.  Remove all items from D."""
        self._cache.clear()
