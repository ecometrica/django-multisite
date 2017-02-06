# -*- coding: utf-8 -*
from __future__ import unicode_literals
from __future__ import absolute_import

import sys

from django.utils import six
from contextlib import contextmanager
from warnings import warn

try:
    from threading import local
except ImportError:
    from django.utils._threading_local import local

from django.core.exceptions import ImproperlyConfigured


_thread_locals = local()


def get_request():
    return getattr(_thread_locals, 'request', None)


class ThreadLocalsMiddleware(object):
    """Middleware that saves request in thread local starage"""
    def process_request(self, request):
        _thread_locals.request = request


class SiteID(local):
    """
    Dynamic settings.SITE_ID replacement, which acts like an integer.

    django.contrib.sites can allow multiple Django sites to share the
    same database. However, they cannot share the same code by
    default.

    SiteID can be used to replace the static settings.SITE_ID integer
    when combined with the appropriate middleware.
    """

    def __init__(self, default=None, *args, **kwargs):
        """
        ``default``, if specified, determines the default SITE_ID,
        if that is unset.
        """
        if default is not None and not isinstance(default, six.integer_types):
            raise ValueError("%r is not a valid default." % default)
        self.default = default
        self.reset()

    def __repr__(self):
        return repr(self.__int__())

    def __str__(self):
        return str(self.__int__())

    def __int__(self):
        if self.site_id is None:
            return self.get_default()
        return self.site_id

    def __lt__(self, other):
        if isinstance(other, six.integer_types):
            return self.__int__() < other
        elif isinstance(other, SiteID):
            return self.__int__() < other.__int__()
        return True

    def __le__(self, other):
        if isinstance(other, six.integer_types):
            return self.__int__() <= other
        elif isinstance(other, SiteID):
            return self.__int__() <= other.__int__()
        return True

    def __eq__(self, other):
        if isinstance(other, six.integer_types):
            return self.__int__() == other
        elif isinstance(other, SiteID):
            return self.__int__() == other.__int__()
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __gt__(self, other):
        return not self.__le__(other)

    def __ge__(self, other):
        return not self.__lt__(other)

    def __hash__(self):
        return self.__int__()

    @contextmanager
    def override(self, value):
        """
        Overrides SITE_ID temporarily::

           >>> with settings.SITE_ID.override(2):
           ...    print settings.SITE_ID
           2
        """
        site_id_original = self.site_id
        self.set(value)
        try:
            yield self
        finally:
            self.site_id = site_id_original

    def set(self, value):
        from django.db.models import Model
        if isinstance(value, Model):
            value = value.pk
        self.site_id = value

    def reset(self):
        self.site_id = None

    def get_default(self):
        """Returns the default SITE_ID."""
        if self.default is None:
            raise ValueError('SITE_ID has not been set.')
        return self.default


class SiteDomain(SiteID):
    def __init__(self, default, *args, **kwargs):
        """
        ``default`` is the default domain name, resolved to SITE_ID, if
        that is unset.
        """
        # make sure they passed us a string; doing this is the single hardest py2/py3 compat headache.
        # http://python-future.org/compatible_idioms.html#basestring and
        # https://github.com/PythonCharmers/python-future/blob/master/src/past/types/basestring.py
        # are not super informative, so just fall back on a literal version check:
        if not isinstance(default, basestring if sys.version_info.major == 2 else str):
            raise TypeError("%r is not a valid default domain." % default)
        self.default_domain = default
        self.default = None
        self.reset()

    def get_default(self):
        """Returns the default SITE_ID that matches the default domain name."""
        from django.contrib.sites.models import Site
        if not Site._meta.installed:
            raise ImproperlyConfigured('django.contrib.sites is not in '
                                       'settings.INSTALLED_APPS')

        if self.default is None:
            qset = Site.objects.only('id')
            self.default = qset.get(domain=self.default_domain).id
        return self.default


def SiteIDHook():
    """Deprecated: Use multisite.SiteID(default=1) for identical behaviour."""
    warn('Use multisite.SiteID instead of multisite.threadlocals.SiteIDHook',
         DeprecationWarning, stacklevel=2)
    return SiteID(default=1)
