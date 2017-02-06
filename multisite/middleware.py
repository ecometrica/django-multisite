# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import absolute_import

import os
import tempfile
try:
    from urlparse import urlsplit, urlunsplit
except ImportError:
    from urllib.parse import urlsplit, urlunsplit

import django
from django.conf import settings
from django.contrib.sites.models import Site, SITE_CACHE
from django.core import mail

try:
    from django.core.cache import caches
except ImportError:
    # Django < 1.7 compatibility
    from django.core.cache import get_cache
else:
    def get_cache(cache_alias):
        return caches[cache_alias]

try:
    # Django > 1.10 uses MiddlewareMixin
    from django.utils.deprecation import MiddlewareMixin
except ImportError:
    MiddlewareMixin = object

from django.core.exceptions import ImproperlyConfigured
from django.core.urlresolvers import get_callable
from django.db.models.signals import pre_save, post_delete, post_init
from django.http import Http404, HttpResponsePermanentRedirect

try:
    # Deprecated in Django 1.5
    from django.utils.hashcompat import md5_constructor
except ImportError:
    # The above has been removed in Django 1.6
    from hashlib import md5 as md5_constructor

from .models import Alias


class DynamicSiteMiddleware(MiddlewareMixin):
    def __init__(self, *args, **kwargs):
        super(DynamicSiteMiddleware, self).__init__(*args, **kwargs)
        if not hasattr(settings.SITE_ID, 'set'):
            raise TypeError('Invalid type for settings.SITE_ID: %s' %
                            type(settings.SITE_ID).__name__)

        self.cache_alias = getattr(settings, 'CACHE_MULTISITE_ALIAS',
                                   'default')
        self.key_prefix = getattr(
            settings,
            'CACHE_MULTISITE_KEY_PREFIX',
            settings.CACHES[self.cache_alias].get('KEY_PREFIX', '')
        )

        self.cache = get_cache(self.cache_alias)
        post_init.connect(self.site_domain_cache_hook, sender=Site,
                          dispatch_uid='multisite_post_init')
        pre_save.connect(self.site_domain_changed_hook, sender=Site)
        post_delete.connect(self.site_deleted_hook, sender=Site)

    def get_cache_key(self, netloc):
        """Returns a cache key based on ``netloc``."""
        netloc = md5_constructor(netloc.encode('utf-8'))
        return 'multisite.alias.%s.%s' % (self.key_prefix,
                                          netloc.hexdigest())

    def netloc_parse(self, netloc):
        """
        Returns ``(host, port)`` for ``netloc`` of the form ``'host:port'``.

        If netloc does not have a port number, ``port`` will be None.
        """
        if ':' in netloc:
            return netloc.rsplit(':', 1)
        else:
            return netloc, None

    def get_development_alias(self, netloc):
        """
        Returns valid Alias when in development mode. Otherwise, returns None.

        Development mode is either:
        - Running tests, i.e. manage.py test
        - Running locally in settings.DEBUG = True, where the hostname is
          a top-level name, i.e. localhost
        """
        # When running tests, django.core.mail.outbox exists and
        # netloc == 'testserver'
        is_testserver = (hasattr(mail, 'outbox') and
                         netloc in ('testserver', 'adminsite.com'))
        # When using runserver, assume that host will only have one path
        # component. This covers 'localhost' and your machine name.
        is_local_debug = (settings.DEBUG and len(netloc.split('.')) == 1)
        if is_testserver or is_local_debug:
            try:
                # Prefer the default SITE_ID
                site_id = settings.SITE_ID.get_default()
                return Alias.canonical.get(site=site_id)
            except ValueError:
                # Fallback to the first Site object
                return Alias.canonical.order_by('site')[0]

    def get_alias(self, netloc):
        """
        Returns Alias matching ``netloc``. Otherwise, returns None.
        """
        host, port = self.netloc_parse(netloc)

        try:
            alias = Alias.objects.resolve(host=host, port=port)
        except ValueError:
            alias = None

        if alias is None:
            # Running under TestCase or runserver?
            return self.get_development_alias(netloc)
        return alias

    def fallback_view(self, request):
        """
        Runs the fallback view function in ``settings.MULTISITE_FALLBACK``.

        If ``MULTISITE_FALLBACK`` is None, raises an Http404 error.

        If ``MULTISITE_FALLBACK`` is callable, will treat that
        callable as a view that returns an HttpResponse.

        If ``MULTISITE_FALLBACK`` is a string, will resolve it to a
        view that returns an HttpResponse.

        In order to use a generic view that takes additional
        parameters, ``settings.MULTISITE_FALLBACK_KWARGS`` may be a
        dictionary of additional keyword arguments.
        """
        fallback = getattr(settings, 'MULTISITE_FALLBACK', None)
        if fallback is None:
            raise Http404
        if callable(fallback):
            view = fallback
        else:
            try:
                view = get_callable(fallback)
                if django.VERSION < (1,8):
                    # older django's get_callable falls through on error,
                    # returning the input as output
                    # which notably is definitely not a callable here
                    if not callable(view):
                        raise ImportError()
            except ImportError:
                # newer django forces this to be an error, which is tidier.
                # we rewrite the error to be a bit more helpful to our users.
                raise ImproperlyConfigured(
                    'settings.MULTISITE_FALLBACK is not callable: %s' %
                    fallback
                )

        kwargs = getattr(settings, 'MULTISITE_FALLBACK_KWARGS', {})
        if hasattr(view, 'as_view'):
            # Class-based view
            return view.as_view(**kwargs)(request)
        # View function
        return view(request, **kwargs)

    def redirect_to_canonical(self, request, alias):
        if not alias.redirect_to_canonical or alias.is_canonical:
            return
        url = urlsplit(request.build_absolute_uri(request.get_full_path()))
        url = urlunsplit((url.scheme,
                          alias.site.domain,
                          url.path, url.query, url.fragment))
        return HttpResponsePermanentRedirect(url)

    def process_request(self, request):
        netloc = request.get_host().lower()
        cache_key = self.get_cache_key(netloc)

        # Find the Alias in the cache
        alias = self.cache.get(cache_key)
        if alias is not None:
            self.cache.set(cache_key, alias)
            settings.SITE_ID.set(alias.site_id)
            return self.redirect_to_canonical(request, alias)

        # Cache missed
        alias = self.get_alias(netloc)

        # Fallback using settings.MULTISITE_FALLBACK
        if alias is None:
            settings.SITE_ID.reset()
            return self.fallback_view(request)

        # Found Site
        self.cache.set(cache_key, alias)
        settings.SITE_ID.set(alias.site_id)
        SITE_CACHE[settings.SITE_ID] = alias.site  # Pre-populate SITE_CACHE
        return self.redirect_to_canonical(request, alias)

    @classmethod
    def site_domain_cache_hook(self, sender, instance, *args, **kwargs):
        """Caches Site.domain in the object for site_domain_changed_hook."""
        instance._domain_cache = instance.domain

    def site_domain_changed_hook(self, sender, instance, raw, *args, **kwargs):
        """Clears the cache if Site.domain has changed."""
        if raw or instance.pk is None:
            return

        original = getattr(instance, '_domain_cache', None)
        if original != instance.domain:
            self.cache.clear()

    def site_deleted_hook(self, *args, **kwargs):
        """Clears the cache if Site was deleted."""
        self.cache.clear()


class CookieDomainMiddleware(object):
    def __init__(self):
        self.depth = int(getattr(settings, 'MULTISITE_COOKIE_DOMAIN_DEPTH', 0))
        if self.depth < 0:
            raise ValueError(
                'Invalid MULTISITE_COOKIE_DOMAIN_DEPTH: {depth!r}'.format(
                    depth=self.depth
                )
            )
        self.psl_cache = getattr(settings,
                                 'MULTISITE_PUBLIC_SUFFIX_LIST_CACHE',
                                 None)
        if self.psl_cache is None:
            self.psl_cache = os.path.join(tempfile.gettempdir(),
                                          'multisite_tld.dat')
        self._tldextract = None

    def tldextract(self, url):
        import tldextract
        if self._tldextract is None:
            self._tldextract = tldextract.TLDExtract(cache_file=self.psl_cache)
        return self._tldextract(url)

    def match_cookies(self, request, response):
        return [c for c in response.cookies.values() if not c['domain']]

    def process_response(self, request, response):
        matched = self.match_cookies(request=request, response=response)
        if not matched:
            return response     # No cookies to edit

        parsed = self.tldextract(request.get_host())
        if not parsed.suffix:
            return response     # IP address or local path
        if not parsed.domain:
            return response     # Only TLD

        subdomains = parsed.subdomain.split('.') if parsed.subdomain else []
        if not self.depth:
            subdomains = ['']
        elif len(subdomains) < self.depth:
            return response     # Not enough subdomain parts
        else:
            subdomains = [''] + subdomains[-self.depth:]

        domain = '.'.join(subdomains + [parsed.domain, parsed.suffix])

        for morsel in matched:
            morsel['domain'] = domain
        return response
