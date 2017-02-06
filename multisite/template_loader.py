# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import absolute_import

from .template.loaders.filesystem import Loader

# The template.loaders.filesystem.Loader class used to live here. Now that
# we have more than one Loader class in the project, they are defined in the
# same fashion as Django's.
# For backward-compatibility reasons, Loader in this file points to what
# used to be defined here.
__all__ = ['Loader']
