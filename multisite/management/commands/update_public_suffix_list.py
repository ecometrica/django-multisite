from __future__ import print_function
from __future__ import unicode_literals
from __future__ import absolute_import

import logging
import os
import tempfile

from django.conf import settings
from django.core.management.base import NoArgsCommand

import tldextract


class Command(NoArgsCommand):
    def handle_noargs(self, **options):
        self.setup_logging(verbosity=options.get('verbosity', 1))

        filename = getattr(
            settings, 'MULTISITE_PUBLIC_SUFFIX_LIST_CACHE',
            os.path.join(tempfile.gettempdir(), 'multisite_tld.dat')
        )
        self.log("Updating {filename}".format(filename=filename))

        extract = tldextract.TLDExtract(cache_file=filename)
        extract.update(fetch_now=True)
        self.log("Done.")

    def setup_logging(self, verbosity):
        self.verbosity = int(verbosity)

        # Mute tldextract's logger
        logger = logging.getLogger('tldextract')
        if self.verbosity < 2:
            logger.setLevel(logging.CRITICAL)

    def log(self, msg, level=2):
        if self.verbosity >= level:
            print(msg)
