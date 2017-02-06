# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import absolute_import

from collections import defaultdict
import hashlib

from django.contrib.sites.models import Site
from django.template.base import TemplateDoesNotExist
from django.template.loader import get_template_from_string
from django.template.loaders.cached import Loader as CachedLoader


class Loader(CachedLoader):
    """
    This is an adaptation of Django's cached template loader. It differs in
    that the cache is domain-based, so you can actually run more than one
    site with one process.

    The load_template() method has been adapted from Django 1.6.
    """

    def __init__(self, *args, **kwargs):
        super(Loader, self).__init__(*args, **kwargs)
        self.template_cache = defaultdict(dict)

    def load_template(self, template_name, template_dirs=None):
        domain = Site.objects.get_current().domain
        key = template_name
        if template_dirs:
            # If template directories were specified, use a hash to differentiate
            key = '-'.join([template_name, hashlib.sha1('|'.join(template_dirs)).hexdigest()])

        try:
            template = self.template_cache[domain][key]
        except KeyError:
            template, origin = self.find_template(template_name, template_dirs)
            if not hasattr(template, 'render'):
                try:
                    template = get_template_from_string(template, origin, template_name)
                except TemplateDoesNotExist:
                    # If compiling the template we found raises TemplateDoesNotExist,
                    # back off to returning the source and display name for the template
                    # we were asked to load. This allows for correct identification (later)
                    # of the actual template that does not exist.
                    return template, origin
            self.template_cache[domain][key] = template
        return template, None

