# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import absolute_import

import os
from django.conf import settings
from django.contrib.sites.models import Site
from django.template.loaders.filesystem import Loader as FilesystemLoader


class Loader(FilesystemLoader):
    def get_template_sources(self, template_name, template_dirs=None):
        domain = Site.objects.get_current().domain
        default_dir = getattr(settings, 'MULTISITE_DEFAULT_TEMPLATE_DIR',
                                        'default')
        for tname in (os.path.join(domain, template_name),
                      os.path.join(default_dir, template_name)):
            for item in super(Loader, self).get_template_sources(tname,
                                                         template_dirs):
                yield item
