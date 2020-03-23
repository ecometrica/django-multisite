"""
Tests for django-multisite.

To run this, use:
$ python -m multisite.tests
or
$ python setup.py test
from the parent directory.

This file uses relative imports and so cannot be run standalone.
"""

from __future__ import unicode_literals
from __future__ import absolute_import

import django
import logging
import os
import pytest
import sys
import tempfile
import warnings
from six import StringIO
from unittest import skipUnless

try:
    from unittest import mock
except ImportError:
    import mock

from django.conf import settings
from django.conf.urls import url
from django.contrib.sites.models import Site
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import call_command
from django.http import Http404, HttpResponse
from django.template.loader import get_template
from django.test import TestCase, override_settings
from django.test.client import RequestFactory as DjangoRequestFactory

from multisite import SiteDomain, SiteID, threadlocals

from .hacks import use_framework_for_site_cache
from .hosts import ALLOWED_HOSTS, AllowedHosts, IterableLazyObject
from .middleware import CookieDomainMiddleware, DynamicSiteMiddleware
from .models import Alias


class RequestFactory(DjangoRequestFactory):
    def __init__(self, host):
        super(RequestFactory, self).__init__()
        self.host = host

    def get(self, path, data={}, host=None, **extra):
        if host is None:
            host = self.host
        return super(RequestFactory, self).get(path=path, data=data,
                                               HTTP_HOST=host, **extra)

@pytest.mark.django_db
@skipUnless(Site._meta.installed,
            'django.contrib.sites is not in settings.INSTALLED_APPS')
@override_settings(
    SITE_ID=SiteID(),
    CACHE_SITES_KEY_PREFIX='__test__',
)
class TestContribSite(TestCase):
    def setUp(self):
        Site.objects.all().delete()
        self.site = Site.objects.create(domain='example.com')
        settings.SITE_ID.set(self.site.id)

    def test_get_current_site(self):
        current_site = Site.objects.get_current()
        self.assertEqual(current_site, self.site)
        self.assertEqual(current_site.id, settings.SITE_ID)

# Because we are a middleware package, we have no views available to test with easily
# So create one:
# (This is only used by test_integration)
urlpatterns = [
    url(r'^domain/$', lambda request, *args, **kwargs: HttpResponse(str(Site.objects.get_current())))
]

@pytest.mark.django_db
@skipUnless(Site._meta.installed,
            'django.contrib.sites is not in settings.INSTALLED_APPS')
@override_settings(
    ALLOWED_SITES=['*'],
    ROOT_URLCONF=__name__, #this means that urlpatterns above is used when .get() is called below.
    SITE_ID=SiteID(default=0),
    CACHE_MULTISITE_ALIAS='multisite',
    CACHES={
        'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'},
        'multisite': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}
    },
    MULTISITE_FALLBACK=None,
    ALLOWED_HOSTS=ALLOWED_HOSTS
)
class DynamicSiteMiddlewareTest(TestCase):
    def setUp(self):
        self.host = 'example.com'
        self.factory = RequestFactory(host=self.host)

        Site.objects.all().delete()
        self.site = Site.objects.create(domain=self.host)
        self.site2 = Site.objects.create(domain='anothersite.example')

    def test_valid_domain(self):
        # Make the request
        request = self.factory.get('/')
        self.assertEqual(DynamicSiteMiddleware().process_request(request), None)
        self.assertEqual(settings.SITE_ID, self.site.pk)
        # Request again
        self.assertEqual(DynamicSiteMiddleware().process_request(request), None)
        self.assertEqual(settings.SITE_ID, self.site.pk)

    def test_valid_domain_port(self):
        # Make the request with a specific port
        request = self.factory.get('/', host=self.host + ':8000')
        self.assertEqual(DynamicSiteMiddleware().process_request(request), None)
        self.assertEqual(settings.SITE_ID, self.site.pk)
        # Request again
        self.assertEqual(DynamicSiteMiddleware().process_request(request), None)
        self.assertEqual(settings.SITE_ID, self.site.pk)

    def test_case_sensitivity(self):
        # Make the request in all uppercase
        request = self.factory.get('/', host=self.host.upper())
        self.assertEqual(DynamicSiteMiddleware().process_request(request), None)
        self.assertEqual(settings.SITE_ID, self.site.pk)

    def test_change_domain(self):
        # Make the request
        request = self.factory.get('/')
        self.assertEqual(DynamicSiteMiddleware().process_request(request), None)
        self.assertEqual(settings.SITE_ID, self.site.pk)
        # Another request with a different site
        request = self.factory.get('/', host=self.site2.domain)
        self.assertEqual(DynamicSiteMiddleware().process_request(request), None)
        self.assertEqual(settings.SITE_ID, self.site2.pk)

    def test_unknown_host(self):
        # Unknown host
        request = self.factory.get('/', host='unknown')
        with self.assertRaises(Http404):
            DynamicSiteMiddleware().process_request(request)
        # The middleware resets SiteID to its default value, as given above, on error.
        self.assertEqual(settings.SITE_ID, 0)

    def test_unknown_hostport(self):
        # Unknown host:port
        request = self.factory.get('/', host='unknown:8000')
        with self.assertRaises(Http404):
            DynamicSiteMiddleware().process_request(request)
        # The middleware resets SiteID to its default value, as given above, on error.
        self.assertEqual(settings.SITE_ID, 0)

    def test_invalid_host(self):
        # Invalid host
        request = self.factory.get('/', host='')
        with self.assertRaises(Http404):
            DynamicSiteMiddleware().process_request(request)

    def test_invalid_hostport(self):
        # Invalid host:port
        request = self.factory.get('/', host=':8000')
        with self.assertRaises(Http404):
            DynamicSiteMiddleware().process_request(request)

    def test_no_sites(self):
        # FIXME: this needs to go into its own TestCase since it requires modifying the fixture to work properly
        # Remove all Sites
        Site.objects.all().delete()
        # Make the request
        request = self.factory.get('/')
        self.assertRaises(Http404,
                          DynamicSiteMiddleware().process_request, request)
        # The middleware resets SiteID to its default value, as given above, on error.
        self.assertEqual(settings.SITE_ID, 0)

    def test_redirect(self):
        host = 'example.org'
        alias = Alias.objects.create(site=self.site, domain=host)
        self.assertTrue(alias.redirect_to_canonical)
        # Make the request
        request = self.factory.get('/path', host=host)
        response = DynamicSiteMiddleware().process_request(request)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'],
                         "http://%s/path" % self.host)

    def test_no_redirect(self):
        host = 'example.org'
        Alias.objects.create(site=self.site, domain=host,
                             redirect_to_canonical=False)
        # Make the request
        request = self.factory.get('/path', host=host)
        self.assertEqual(DynamicSiteMiddleware().process_request(request), None)
        self.assertEqual(settings.SITE_ID, self.site.pk)

    def test_integration(self):
        """
        Test that the middleware loads and runs properly under settings.MIDDLEWARE.
        """
        resp = self.client.get('/domain/', HTTP_HOST=self.host)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.site.domain)
        self.assertEqual(settings.SITE_ID, self.site.pk)

        resp = self.client.get('/domain/', HTTP_HOST=self.site2.domain)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.site2.domain)
        self.assertEqual(settings.SITE_ID, self.site2.pk)


@pytest.mark.django_db
@skipUnless(Site._meta.installed,
            'django.contrib.sites is not in settings.INSTALLED_APPS')
@override_settings(
    SITE_ID=SiteID(default=0),
    CACHE_MULTISITE_ALIAS='multisite',
    CACHES={
        'multisite': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}
    },    MULTISITE_FALLBACK=None,
    MULTISITE_FALLBACK_KWARGS={},
)
class DynamicSiteMiddlewareFallbackTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory(host='unknown')

        Site.objects.all().delete()

    def test_404(self):
        request = self.factory.get('/')
        self.assertRaises(Http404,
                          DynamicSiteMiddleware().process_request, request)
        self.assertEqual(settings.SITE_ID, 0)

    def test_testserver(self):
        host = 'testserver'
        site = Site.objects.create(domain=host)
        request = self.factory.get('/', host=host)
        self.assertEqual(DynamicSiteMiddleware().process_request(request), None)
        self.assertEqual(settings.SITE_ID, site.pk)

    def test_string_class(self):
        # Class based
        settings.MULTISITE_FALLBACK = 'django.views.generic.base.RedirectView'
        settings.MULTISITE_FALLBACK_KWARGS = {'url': 'http://example.com/',
                                              'permanent': False}
        request = self.factory.get('/')
        response = DynamicSiteMiddleware().process_request(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'],
                         settings.MULTISITE_FALLBACK_KWARGS['url'])

    def test_class_view(self):
        from django.views.generic.base import RedirectView
        settings.MULTISITE_FALLBACK = RedirectView.as_view(
            url='http://example.com/', permanent=False
        )
        request = self.factory.get('/')
        response = DynamicSiteMiddleware().process_request(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'http://example.com/')

    def test_invalid(self):
        settings.MULTISITE_FALLBACK = ''
        request = self.factory.get('/')
        self.assertRaises(ImproperlyConfigured,
                          DynamicSiteMiddleware().process_request, request)


@pytest.mark.django_db
@skipUnless(Site._meta.installed,
            'django.contrib.sites is not in settings.INSTALLED_APPS')
@override_settings(SITE_ID=0,)
class DynamicSiteMiddlewareSettingsTest(TestCase):
    def test_invalid_settings(self):
        self.assertRaises(TypeError, DynamicSiteMiddleware)


@pytest.mark.django_db
@override_settings(
    SITE_ID=SiteID(default=0),
    CACHE_MULTISITE_ALIAS='multisite',
    CACHES={
        'multisite': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}
    },
    MULTISITE_FALLBACK=None,
    ALLOWED_HOSTS=ALLOWED_HOSTS
)
class CacheTest(TestCase):
    def setUp(self):
        self.host = 'example.com'
        self.factory = RequestFactory(host=self.host)

        Site.objects.all().delete()
        self.site = Site.objects.create(domain=self.host)

    def test_site_domain_changed(self):
        # Test to ensure that the cache is cleared properly
        middleware = DynamicSiteMiddleware()
        cache_key = middleware.get_cache_key(self.host)
        self.assertEqual(middleware.cache.get(cache_key), None)
        # Make the request
        request = self.factory.get('/')
        self.assertEqual(middleware.process_request(request), None)
        self.assertEqual(middleware.cache.get(cache_key).site_id,
                         self.site.pk)
        # Change the domain name
        self.site.domain = 'example.org'
        self.site.save()
        self.assertEqual(middleware.cache.get(cache_key), None)
        # Make the request again, which will now be invalid
        request = self.factory.get('/')
        self.assertRaises(Http404,
                          middleware.process_request, request)
        self.assertEqual(settings.SITE_ID, 0)


@pytest.mark.django_db
@skipUnless(Site._meta.installed,
            'django.contrib.sites is not in settings.INSTALLED_APPS')
@override_settings(SITE_ID=SiteID(),)
class SiteCacheTest(TestCase):

    def _initialize_cache(self):
        # initialize cache again so override key prefix settings are used
        from django.contrib.sites import models
        use_framework_for_site_cache()
        self.cache = models.SITE_CACHE

    def setUp(self):
        from django.contrib.sites import models

        if hasattr(models, 'clear_site_cache'):
            # Before Django 1.6, the Site cache is cleared after the Site
            # object has been created. This replicates that behaviour.
            def save(self, *args, **kwargs):
                super(models.Site, self).save(*args, **kwargs)
                models.SITE_CACHE.clear()
            models.Site.save = save

        self._initialize_cache()
        Site.objects.all().delete()
        self.host = 'example.com'
        self.site = Site.objects.create(domain=self.host)
        settings.SITE_ID.set(self.site.id)

    def test_get_current(self):
        self.assertRaises(KeyError, self.cache.__getitem__, self.site.id)
        # Populate cache
        self.assertEqual(Site.objects.get_current(), self.site)
        self.assertEqual(self.cache[self.site.id], self.site)
        self.assertEqual(self.cache.get(key=self.site.id), self.site)
        self.assertEqual(self.cache.get(key=-1),
                         None)                         # Site doesn't exist
        self.assertEqual(self.cache.get(-1, 'Default'),
                         'Default')                    # Site doesn't exist
        self.assertEqual(self.cache.get(key=-1, default='Non-existant'),
                         'Non-existant')               # Site doesn't exist
        self.assertEqual('Non-existant',
                         self.cache.get(self.site.id, default='Non-existant',
                                        version=100))  # Wrong key version 3
        # Clear cache
        self.cache.clear()
        self.assertRaises(KeyError, self.cache.__getitem__, self.site.id)
        self.assertEqual(self.cache.get(key=self.site.id, default='Cleared'),
                         'Cleared')

    def test_create_site(self):
        self.assertEqual(Site.objects.get_current(), self.site)
        self.assertEqual(Site.objects.get_current().domain, self.site.domain)
        # Create new site
        site = Site.objects.create(domain='example.org')
        settings.SITE_ID.set(site.id)
        self.assertEqual(Site.objects.get_current(), site)
        self.assertEqual(Site.objects.get_current().domain, site.domain)

    def test_change_site(self):
        self.assertEqual(Site.objects.get_current(), self.site)
        self.assertEqual(Site.objects.get_current().domain, self.site.domain)
        # Change site domain
        self.site.domain = 'example.org'
        self.site.save()
        self.assertEqual(Site.objects.get_current(), self.site)
        self.assertEqual(Site.objects.get_current().domain, self.site.domain)

    def test_delete_site(self):
        self.assertEqual(Site.objects.get_current(), self.site)
        self.assertEqual(Site.objects.get_current().domain, self.site.domain)
        # Delete site
        self.site.delete()
        self.assertRaises(KeyError, self.cache.__getitem__, self.site.id)

    @override_settings(CACHE_MULTISITE_KEY_PREFIX="__test__")
    def test_multisite_key_prefix(self):
        self._initialize_cache()
        # Populate cache
        self.assertEqual(Site.objects.get_current(), self.site)
        self.assertEqual(self.cache[self.site.id], self.site)
        self.assertEqual(
            self.cache._cache._get_cache_key(self.site.id),
            'sites.{}.{}'.format(
                settings.CACHE_MULTISITE_KEY_PREFIX, self.site.id
            ),
            self.cache._cache._get_cache_key(self.site.id)
        )

    @override_settings(
        CACHE_MULTISITE_ALIAS='multisite',
        CACHES={
            'multisite': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'KEY_PREFIX': 'looselycoupled'
            }
        },
    )
    def test_default_key_prefix(self):
        """
        If CACHE_MULTISITE_KEY_PREFIX is undefined,
        the caching system should use CACHES[current]['KEY_PREFIX'].
        """
        self._initialize_cache()
        # Populate cache
        self.assertEqual(Site.objects.get_current(), self.site)
        self.assertEqual(self.cache[self.site.id], self.site)
        self.assertEqual(
            self.cache._cache._get_cache_key(self.site.id),
            "sites.looselycoupled.{}".format(self.site.id)
        )

    @override_settings(
        CACHE_MULTISITE_KEY_PREFIX="virtuouslyvirtual",
        )
    def test_multisite_key_prefix_takes_priority_over_default(self):
        self._initialize_cache()
        # Populate cache
        self.assertEqual(Site.objects.get_current(), self.site)
        self.assertEqual(self.cache[self.site.id], self.site)
        self.assertEqual(
            self.cache._cache._get_cache_key(self.site.id),
            "sites.virtuouslyvirtual.{}".format(self.site.id)
        )


@pytest.mark.django_db
class TestSiteID(TestCase):
    def setUp(self):
        Site.objects.all().delete()
        self.site = Site.objects.create(domain='example.com')
        self.site_id = SiteID()

    def test_invalid_default(self):
        self.assertRaises(ValueError, SiteID, default='a')
        self.assertRaises(ValueError, SiteID, default=self.site_id)

    def test_compare_default_site_id(self):
        self.site_id = SiteID(default=self.site.id)
        self.assertEqual(self.site_id, self.site.id)
        self.assertFalse(self.site_id != self.site.id)
        self.assertFalse(self.site_id < self.site.id)
        self.assertTrue(self.site_id <= self.site.id)
        self.assertFalse(self.site_id > self.site.id)
        self.assertTrue(self.site_id >= self.site.id)

    def test_compare_site_ids(self):
        self.site_id.set(1)
        self.assertEqual(self.site_id, self.site_id)
        self.assertFalse(self.site_id != self.site_id)
        self.assertFalse(self.site_id < self.site_id)
        self.assertTrue(self.site_id <= self.site_id)
        self.assertFalse(self.site_id > self.site_id)
        self.assertTrue(self.site_id >= self.site_id)

    def test_compare_differing_types(self):
        self.site_id.set(1)
        self.assertNotEqual(self.site_id, '1')
        self.assertFalse(self.site_id == '1')
        self.assertTrue(self.site_id < '1')
        self.assertTrue(self.site_id <= '1')
        self.assertFalse(self.site_id > '1')
        self.assertFalse(self.site_id >= '1')
        self.assertNotEqual('1', self.site_id)
        self.assertFalse('1' == self.site_id)
        self.assertFalse('1' < self.site_id)
        self.assertFalse('1' <= self.site_id)
        self.assertTrue('1' > self.site_id)
        self.assertTrue('1' >= self.site_id)

    def test_set(self):
        self.site_id.set(10)
        self.assertEqual(int(self.site_id), 10)
        self.site_id.set(20)
        self.assertEqual(int(self.site_id), 20)
        self.site_id.set(self.site)
        self.assertEqual(int(self.site_id), self.site.id)

    def test_hash(self):
        self.site_id.set(10)
        self.assertEqual(hash(self.site_id), 10)
        self.site_id.set(20)
        self.assertEqual(hash(self.site_id), 20)

    def test_str_repr(self):
        self.site_id.set(10)
        self.assertEqual(str(self.site_id), '10')
        self.assertEqual(repr(self.site_id), '10')

    def test_context_manager(self):
        self.assertEqual(self.site_id.site_id, None)
        with self.site_id.override(1):
            self.assertEqual(self.site_id.site_id, 1)
            with self.site_id.override(2):
                self.assertEqual(self.site_id.site_id, 2)
            self.assertEqual(self.site_id.site_id, 1)
        self.assertEqual(self.site_id.site_id, None)


@pytest.mark.django_db
@skipUnless(Site._meta.installed,
            'django.contrib.sites is not in settings.INSTALLED_APPS')
class TestSiteDomain(TestCase):
    def setUp(self):
        Site.objects.all().delete()
        self.domain = 'example.com'
        self.site = Site.objects.create(domain=self.domain)

    def test_init(self):
        self.assertEqual(int(SiteDomain(default=self.domain)), self.site.id)
        self.assertRaises(Site.DoesNotExist,
                          int, SiteDomain(default='invalid'))
        self.assertRaises(TypeError, SiteDomain, default=None)
        self.assertRaises(TypeError, SiteDomain, default=1)

    def test_deferred_site(self):
        domain = 'example.org'
        self.assertRaises(Site.DoesNotExist,
                          int, SiteDomain(default=domain))
        site = Site.objects.create(domain=domain)
        self.assertEqual(int(SiteDomain(default=domain)),
                         site.id)


@pytest.mark.django_db
class AliasTest(TestCase):
    def setUp(self):
        Alias.objects.all().delete()
        Site.objects.all().delete()

    def test_create(self):
        site0 = Site.objects.create()
        site1 = Site.objects.create(domain='1.example')
        site2 = Site.objects.create(domain='2.example')
        # Missing site
        self.assertRaises(ValidationError, Alias.objects.create)
        self.assertRaises(ValidationError,
                          Alias.objects.create, domain='0.example')
        # Valid
        self.assertTrue(Alias.objects.create(domain='1a.example', site=site1))
        # Duplicate domain
        self.assertRaises(
            ValidationError,
            Alias.objects.create, domain=site1.domain, site=site1
        )
        self.assertRaises(
            ValidationError,
            Alias.objects.create, domain=site2.domain, site=site1
        )
        self.assertRaises(
            ValidationError,
            Alias.objects.create, domain='1a.example', site=site1
        )
        # Duplicate domains, case-sensitivity
        self.assertRaises(
            ValidationError,
            Alias.objects.create, domain='1A.EXAMPLE', site=site2
        )
        self.assertRaises(
            ValidationError,
            Alias.objects.create, domain='2.EXAMPLE', site=site2
        )
        # Duplicate is_canonical
        site1.domain = '1b.example'
        self.assertRaises(
            ValidationError,
            Alias.objects.create,
            domain=site1.domain, site=site1, is_canonical=True
        )
        # Invalid is_canonical
        self.assertRaises(
            ValidationError,
            Alias.objects.create,
            domain=site1.domain, site=site1, is_canonical=False
        )

    def test_repr(self):
        site = Site.objects.create(domain='example.com')
        self.assertEqual(repr(Alias.objects.get(site=site)),
                         u'<Alias: %(domain)s -> %(domain)s>' % site.__dict__)

    def test_managers(self):
        site = Site.objects.create(domain='example.com')
        Alias.objects.create(site=site, domain='example.org')
        self.assertEqual(set(Alias.objects.values_list('domain', flat=True)),
                         set(['example.com', 'example.org']))
        self.assertEqual(set(Alias.canonical.values_list('domain', flat=True)),
                         set(['example.com']))
        self.assertEqual(set(Alias.aliases.values_list('domain', flat=True)),
                         set(['example.org']))

    def test_sync_many(self):
        # Create Sites with Aliases
        Site.objects.create()
        site1 = Site.objects.create(domain='1.example.com')
        site2 = Site.objects.create(domain='2.example.com')
        # Create Site without triggering signals
        site3 = Site(domain='3.example.com')
        site3.save_base(raw=True)
        self.assertEqual(set(Alias.objects.values_list('domain', flat=True)),
                         set([site1.domain, site2.domain]))
        # Sync existing
        site1.domain = '1.example.org'
        site1.save_base(raw=True)
        site2.domain = '2.example.org'
        site2.save_base(raw=True)
        Alias.canonical.sync_many()
        self.assertEqual(set(Alias.objects.values_list('domain', flat=True)),
                         set([site1.domain, site2.domain]))
        # Sync with filter
        site1.domain = '1.example.net'
        site1.save_base(raw=True)
        site2.domain = '2.example.net'
        site2.save_base(raw=True)
        Alias.canonical.sync_many(site__domain=site1.domain)
        self.assertEqual(set(Alias.objects.values_list('domain', flat=True)),
                         set([site1.domain, '2.example.org']))

    def test_sync_missing(self):
        Site.objects.create()
        site1 = Site.objects.create(domain='1.example.com')
        # Update site1 without triggering signals
        site1.domain = '1.example.org'
        site1.save_base(raw=True)
        # Create site2 without triggering signals
        site2 = Site(domain='2.example.org')
        site2.save_base(raw=True)
        # Only site2 should be updated
        Alias.canonical.sync_missing()
        self.assertEqual(set(Alias.objects.values_list('domain', flat=True)),
                         set(['1.example.com', site2.domain]))

    def test_sync_all(self):
        Site.objects.create()
        site1 = Site.objects.create(domain='1.example.com')
        # Update site1 without triggering signals
        site1.domain = '1.example.org'
        site1.save_base(raw=True)
        # Create site2 without triggering signals
        site2 = Site(domain='2.example.org')
        site2.save_base(raw=True)
        # Sync all
        Alias.canonical.sync_all()
        self.assertEqual(set(Alias.objects.values_list('domain', flat=True)),
                         set([site1.domain, site2.domain]))

    def test_sync(self):
        # Create Site without triggering signals
        site = Site(domain='example.com')
        site.save_base(raw=True)
        # Insert Alias
        self.assertFalse(Alias.objects.filter(site=site).exists())
        Alias.sync(site=site)
        self.assertEqual(Alias.objects.get(site=site).domain, site.domain)
        # Idempotent sync_alias
        Alias.sync(site=site)
        self.assertEqual(Alias.objects.get(site=site).domain, site.domain)
        # Duplicate force_insert
        self.assertRaises(ValidationError,
                          Alias.sync, site=site, force_insert=True)
        # Update Alias
        site.domain = 'example.org'
        Alias.sync(site=site)
        self.assertEqual(Alias.objects.get(site=site).domain, site.domain)
        # Clear domain
        site.domain = ''
        Alias.sync(site=site)
        self.assertFalse(Alias.objects.filter(site=site).exists())

    def test_sync_blank_domain(self):
        # Create Site
        site = Site.objects.create(domain='example.com')
        # Without clearing domain
        self.assertRaises(ValueError, Alias._sync_blank_domain, site)
        # With an extra Alias
        site.domain = ''
        alias = Alias.objects.create(site=site, domain='example.org')
        self.assertRaises(Alias.MultipleObjectsReturned,
                          Alias._sync_blank_domain, site)
        # With a blank site
        alias.delete()
        Alias._sync_blank_domain(site)
        self.assertFalse(Alias.objects.filter(site=site).exists())

    def test_hooks(self):
        # Create empty Site
        Site.objects.create()
        self.assertFalse(Alias.objects.filter(domain='').exists())
        # Create Site
        site = Site.objects.create(domain='example.com')
        alias = Alias.objects.get(site=site)
        self.assertEqual(alias.domain, site.domain)
        self.assertTrue(alias.is_canonical)
        # Create a non-canonical alias
        Alias.objects.create(site=site, domain='example.info')
        # Change Site to another domain name
        site.domain = 'example.org'
        site.save()
        self.assertEqual(Alias.canonical.get(site=site).domain, site.domain)
        self.assertEqual(Alias.aliases.get(site=site).domain, 'example.info')
        # Change Site to an empty domain name
        site.domain = ''
        self.assertRaises(Alias.MultipleObjectsReturned, site.save)
        Alias.aliases.all().delete()
        Site.objects.get(domain='').delete()  # domain is unique in Django1.9
        site.save()
        self.assertFalse(Alias.objects.filter(site=site).exists())
        # Change Site from an empty domain name
        site.domain = 'example.net'
        site.save()
        self.assertEqual(Alias.canonical.get(site=site).domain, site.domain)
        # Delete Site
        site.delete()
        self.assertFalse(Alias.objects.filter(site=site).exists())

    def test_expand_netloc(self):
        _expand_netloc = Alias.objects._expand_netloc
        self.assertRaises(ValueError, _expand_netloc, '')
        self.assertRaises(ValueError, _expand_netloc, '', 8000)
        self.assertEqual(_expand_netloc('testserver', 8000),
                         ['testserver:8000', 'testserver',
                          '*:8000', '*'])
        self.assertEqual(_expand_netloc('testserver'),
                         ['testserver', '*'])
        self.assertEqual(_expand_netloc('example.com', 8000),
                         ['example.com:8000', 'example.com',
                          '*.com:8000', '*.com',
                          '*:8000', '*'])
        self.assertEqual(_expand_netloc('example.com'),
                         ['example.com', '*.com', '*'])
        self.assertEqual(_expand_netloc('www.example.com', 8000),
                         ['www.example.com:8000', 'www.example.com',
                          '*.example.com:8000', '*.example.com',
                          '*.com:8000', '*.com',
                          '*:8000', '*'])
        self.assertEqual(_expand_netloc('www.example.com'),
                         ['www.example.com', '*.example.com', '*.com', '*'])

    def test_resolve(self):
        site = Site.objects.create(domain='example.com')
        # *.example.com
        self.assertEqual(Alias.objects.resolve('www.example.com'),
                         None)
        self.assertEqual(Alias.objects.resolve('www.dev.example.com'),
                         None)
        alias = Alias.objects.create(site=site, domain='*.example.com')
        self.assertEqual(Alias.objects.resolve('www.example.com'),
                         alias)
        self.assertEqual(Alias.objects.resolve('www.dev.example.com'),
                         alias)
        # *
        self.assertEqual(Alias.objects.resolve('example.net'),
                         None)
        alias = Alias.objects.create(site=site, domain='*')
        self.assertEqual(Alias.objects.resolve('example.net'),
                         alias)



@pytest.mark.django_db
@override_settings(
    MULTISITE_COOKIE_DOMAIN_DEPTH=0,
    MULTISITE_PUBLIC_SUFFIX_LIST_CACHE=None,
    ALLOWED_HOSTS=ALLOWED_HOSTS,
    MULTISITE_EXTRA_HOSTS=['.extrahost.com']
)
class TestCookieDomainMiddleware(TestCase):

    def setUp(self):
        self.factory = RequestFactory(host='example.com')
        Site.objects.all().delete()
        # create sites so we populate ALLOWED_HOSTS
        Site.objects.create(domain='example.com')
        Site.objects.create(domain='test.example.com')
        Site.objects.create(domain='app.test1.example.com')
        Site.objects.create(domain='app.test2.example.com')
        Site.objects.create(domain='new.app.test3.example.com')

    def test_init(self):
        self.assertEqual(CookieDomainMiddleware().depth, 0)
        self.assertEqual(CookieDomainMiddleware().psl_cache,
                         os.path.join(tempfile.gettempdir(),
                                      'multisite_tld.dat'))

        with override_settings(MULTISITE_COOKIE_DOMAIN_DEPTH=1,
                               MULTISITE_PUBLIC_SUFFIX_LIST_CACHE='/var/psl'):
            middleware = CookieDomainMiddleware()
            self.assertEqual(middleware.depth, 1)
            self.assertEqual(middleware.psl_cache, '/var/psl')

        with override_settings(MULTISITE_COOKIE_DOMAIN_DEPTH=-1):
            self.assertRaises(ValueError, CookieDomainMiddleware)

        with override_settings(MULTISITE_COOKIE_DOMAIN_DEPTH='invalid'):
            self.assertRaises(ValueError, CookieDomainMiddleware)

    def test_no_matched_cookies(self):
        # No cookies
        request = self.factory.get('/')
        response = HttpResponse()
        self.assertEqual(CookieDomainMiddleware().match_cookies(request, response),
                         [])
        cookies = CookieDomainMiddleware().process_response(request, response).cookies
        self.assertEqual(list(cookies.values()), [])

        # Add some cookies with their domains already set
        response.set_cookie(key='a', value='a', domain='.example.org')
        response.set_cookie(key='b', value='b', domain='.example.co.uk')
        self.assertEqual(CookieDomainMiddleware().match_cookies(request, response),
                         [])
        cookies = CookieDomainMiddleware().process_response(request, response).cookies

        if sys.version_info.major < 3:  # for testing under Python 2.X
            self.assertItemsEqual(
                list(cookies.values()), [cookies['a'], cookies['b']]
            )
        else:
            self.assertCountEqual(
                list(cookies.values()), [cookies['a'], cookies['b']]
            )
        self.assertEqual(cookies['a']['domain'], '.example.org')
        self.assertEqual(cookies['b']['domain'], '.example.co.uk')

    def test_matched_cookies(self):
        request = self.factory.get('/')
        response = HttpResponse()
        response.set_cookie(key='a', value='a', domain=None)
        self.assertEqual(CookieDomainMiddleware().match_cookies(request, response),
                         [response.cookies['a']])
        # No new cookies should be introduced
        cookies = CookieDomainMiddleware().process_response(request, response).cookies
        self.assertEqual(list(cookies.values()), [cookies['a']])

    def test_ip_address(self):
        response = HttpResponse()
        response.set_cookie(key='a', value='a', domain=None)
        allowed = [host for host in ALLOWED_HOSTS] + ['192.0.43.10']
        # IP addresses should not be mutated
        with override_settings(ALLOWED_HOSTS=allowed):
            request = self.factory.get('/', host='192.0.43.10')
            cookies = CookieDomainMiddleware().process_response(request, response).cookies
        self.assertEqual(cookies['a']['domain'], '')

    def test_localpath(self):
        response = HttpResponse()
        response.set_cookie(key='a', value='a', domain=None)

        allowed = [host for host in ALLOWED_HOSTS] + \
                  ['localhost', 'localhost.localdomain']
        with override_settings(ALLOWED_HOSTS=allowed):
            # Local domains should not be mutated
            request = self.factory.get('/', host='localhost')
            cookies = CookieDomainMiddleware().process_response(request, response).cookies
            self.assertEqual(cookies['a']['domain'], '')
            # Even local subdomains
            request = self.factory.get('/', host='localhost.localdomain')
            cookies = CookieDomainMiddleware().process_response(request, response).cookies
        self.assertEqual(cookies['a']['domain'], '')

    def test_simple_tld(self):
        response = HttpResponse()
        response.set_cookie(key='a', value='a', domain=None)

        allowed = [host for host in ALLOWED_HOSTS] + \
                  ['ai', 'www.ai']
        with override_settings(ALLOWED_HOSTS=allowed):
            # Top-level domains shouldn't get mutated
            request = self.factory.get('/', host='ai')
            cookies = CookieDomainMiddleware().process_response(request, response).cookies
            self.assertEqual(cookies['a']['domain'], '')
            # Domains inside a TLD are OK
            request = self.factory.get('/', host='www.ai')
            cookies = CookieDomainMiddleware().process_response(request, response).cookies
        self.assertEqual(cookies['a']['domain'], '.www.ai')

    def test_effective_tld(self):
        response = HttpResponse()
        response.set_cookie(key='a', value='a', domain=None)

        allowed = [host for host in ALLOWED_HOSTS] + \
                  ['com.ai', 'nic.com.ai']
        with override_settings(ALLOWED_HOSTS=allowed):
            # Effective top-level domains with a webserver shouldn't get mutated
            request = self.factory.get('/', host='com.ai')
            cookies = CookieDomainMiddleware().process_response(request, response).cookies
            self.assertEqual(cookies['a']['domain'], '')
            # Domains within an effective TLD are OK
            request = self.factory.get('/', host='nic.com.ai')
            cookies = CookieDomainMiddleware().process_response(request, response).cookies
        self.assertEqual(cookies['a']['domain'], '.nic.com.ai')

    def test_subdomain_depth(self):
        response = HttpResponse()
        response.set_cookie(key='a', value='a', domain=None)

        allowed = [host for host in ALLOWED_HOSTS] + ['com']
        with override_settings(
                MULTISITE_COOKIE_DOMAIN_DEPTH=1, ALLOWED_HOSTS=allowed
        ):
            # At depth 1:
            middleware = CookieDomainMiddleware()
            # Top-level domains are ignored
            request = self.factory.get('/', host='com')
            cookies = middleware.process_response(request, response).cookies
            self.assertEqual(cookies['a']['domain'], '')
            # As are domains within a TLD
            request = self.factory.get('/', host='example.com')
            cookies = middleware.process_response(request, response).cookies
            self.assertEqual(cookies['a']['domain'], '')
            # But subdomains will get matched
            request = self.factory.get('/', host='test.example.com')
            cookies = middleware.process_response(request, response).cookies
            self.assertEqual(cookies['a']['domain'], '.test.example.com')
            # And sub-subdomains will get matched to 1 level deep
            cookies['a']['domain'] = ''
            request = self.factory.get('/', host='app.test1.example.com')
            cookies = middleware.process_response(request, response).cookies
            self.assertEqual(cookies['a']['domain'], '.test1.example.com')

    def test_subdomain_depth_2(self):
        response = HttpResponse()
        response.set_cookie(key='a', value='a', domain=None)
        with override_settings(MULTISITE_COOKIE_DOMAIN_DEPTH=2):
            # At MULTISITE_COOKIE_DOMAIN_DEPTH 2, subdomains are matched to
            # 2 levels deep
            middleware = CookieDomainMiddleware()
            request = self.factory.get('/', host='app.test2.example.com')
            cookies = middleware.process_response(request, response).cookies
            self.assertEqual(cookies['a']['domain'], '.app.test2.example.com')
            cookies['a']['domain'] = ''
            request = self.factory.get('/', host='new.app.test3.example.com')
            cookies = middleware.process_response(request, response).cookies
            self.assertEqual(cookies['a']['domain'], '.app.test3.example.com')

    def test_wildcard_subdomains(self):
        response = HttpResponse()
        response.set_cookie(key='a', value='a', domain=None)

        allowed = [host for host in ALLOWED_HOSTS] + ['.test.example.com']
        with override_settings(
                MULTISITE_COOKIE_DOMAIN_DEPTH=2, ALLOWED_HOSTS=allowed
        ):
            # At MULTISITE_COOKIE_DOMAIN_DEPTH 2, subdomains are matched to
            # 2 levels deep against the wildcard
            middleware = CookieDomainMiddleware()
            request = self.factory.get('/', host='foo.test.example.com')
            cookies = middleware.process_response(request, response).cookies
            self.assertEqual(cookies['a']['domain'], '.foo.test.example.com')
            cookies['a']['domain'] = ''
            request = self.factory.get('/', host='foo.bar.test.example.com')
            cookies = middleware.process_response(request, response).cookies
            self.assertEqual(cookies['a']['domain'], '.bar.test.example.com')

    def test_multisite_extra_hosts(self):
        # MULTISITE_EXTRA_HOSTS is set to ['.extrahost.com'] but
        # ALLOWED_HOSTS seems to be genereated in override_settings before
        # the extra hosts is added, so we need to recalculate it here.
        allowed = IterableLazyObject(lambda: AllowedHosts())
        with override_settings(ALLOWED_HOSTS=allowed):
            response = HttpResponse()
            response.set_cookie(key='a', value='a', domain=None)
            middleware = CookieDomainMiddleware()
            request = self.factory.get('/', host='test.extrahost.com')
            cookies = middleware.process_response(request, response).cookies
            self.assertEqual(cookies['a']['domain'], '.extrahost.com')
            cookies['a']['domain'] = ''
            request = self.factory.get('/', host='foo.extrahost.com')
            cookies = middleware.process_response(request, response).cookies
            self.assertEqual(cookies['a']['domain'], '.extrahost.com')
            cookies['a']['domain'] = ''
            request = self.factory.get('/', host='foo.bar.extrahost.com')
            cookies = middleware.process_response(request, response).cookies
            self.assertEqual(cookies['a']['domain'], '.extrahost.com')


if django.VERSION < (1, 8):
    TEMPLATE_SETTINGS = {
        'TEMPLATE_LOADERS': ['multisite.template.loaders.filesystem.Loader'],
        'TEMPLATE_DIRS': [os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                  'test_templates')]
    }
else:
    TEMPLATE_SETTINGS = {'TEMPLATES':[
        {
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'DIRS': [
                os.path.join(os.path.abspath(os.path.dirname(__file__)),
                             'test_templates')
            ],
            'OPTIONS': {
                'loaders': [
                    'multisite.template.loaders.filesystem.Loader',
                ]
            },
        }
    ]
    }


@override_settings(
    MULTISITE_DEFAULT_TEMPLATE_DIR='multisite_templates',
    **TEMPLATE_SETTINGS
)
class TemplateLoaderTests(TestCase):

    def test_get_template_multisite_default_dir(self):
        template = get_template("test.html")
        self.assertEqual(template.render(), "Test!")

    def test_domain_template(self):
        template = get_template("example.html")
        self.assertEqual(template.render(), "Test example.com template")

    def test_get_template_old_settings(self):
        # tests that we can still get to the template filesystem loader with
        # the old setting configuration
        with override_settings(
                TEMPLATES=[
                    {
                        'BACKEND': 'django.template.backends.django.DjangoTemplates',
                        'DIRS': [
                            os.path.join(
                                os.path.abspath(os.path.dirname(__file__)),
                                'test_templates')
                        ],
                        'OPTIONS': {
                            'loaders': [
                                'multisite.template_loader.Loader',
                            ]
                        },
                    }
                ]
        ):
            template = get_template("test.html")
            self.assertEqual(template.render(), "Test!")


class UpdatePublicSuffixListCommandTestCase(TestCase):

    def setUp(self):
        self.cache_file = os.path.join(tempfile.gettempdir(), "multisite_tld.dat")
        # save the tldextract logger output to a buffer to test output
        self.out = StringIO()
        self.logger = logging.getLogger('tldextract')
        self.logger.setLevel(logging.DEBUG)
        stdout_handler = logging.StreamHandler(self.out)
        stdout_handler.setLevel(logging.DEBUG)
        self.logger.addHandler(stdout_handler)

        # patch tldextract to avoid actual requests
        self.patcher = mock.patch('tldextract.TLDExtract')
        self.tldextract = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def tldextract_update_side_effect(self, *args, **kwargs):
        self.logger.debug('TLDExtract.update called')

    def test_command(self):
        call_command('update_public_suffix_list')
        expected_calls = [
            mock.call(cache_file=self.cache_file),
            mock.call().update(fetch_now=True)
        ]
        self.assertEqual(self.tldextract.mock_calls, expected_calls)

    def test_command_output(self):
        # make sure that the logger receives output from the method
        self.tldextract().update.side_effect = self.tldextract_update_side_effect

        call_command('update_public_suffix_list', verbosity=3)
        update_message = 'Updating {}'.format(self.cache_file)
        self.assertIn(update_message, self.out.getvalue())
        self.assertIn('TLDExtract.update called', self.out.getvalue())
