# -*- coding: utf-8 -*
from __future__ import unicode_literals
from __future__ import absolute_import

from warnings import warn

from django.db import models
from django.contrib.sites import managers
from django.db.models.fields import FieldDoesNotExist
from django.db.models.sql import constants


class SpanningCurrentSiteManager(managers.CurrentSiteManager):
    """As opposed to django.contrib.sites.managers.CurrentSiteManager, this
    CurrentSiteManager can span multiple related models by using the django
    filtering syntax, namely foo__bar__baz__site.

    For example, let's say you have a model called Layer, which has a field
    called family, which points to a model called LayerFamily, which in
    turn has a field called site pointing to a django.contrib.sites Site
    model. On Layer, add the following manager:

        on_site = SpanningCurrentSiteManager("family__site")

    and it will do the proper thing."""

    def _validate_field_name(self):
        """Given the field identifier, goes down the chain to check that
        each specified field
            a) exists,
            b) is of type ForeignKey or ManyToManyField

        If no field name is specified when instantiating
        SpanningCurrentSiteManager, it tries to find either 'site' or
        'sites' as the site link, much like CurrentSiteManager does.
        """
        if self._CurrentSiteManager__field_name is None:
            # Guess at field name
            field_names = self.model._meta.get_all_field_names()
            for potential_name in ['site', 'sites']:
                if potential_name in field_names:
                    self._CurrentSiteManager__field_name = potential_name
                    break
            else:
                raise ValueError(
                    "%s couldn't find a field named either 'site' or 'sites' "
                    "in %s." %
                    (self.__class__.__name__, self.model._meta.object_name)
                )

        fieldname_chain = self._CurrentSiteManager__field_name.split(
            constants.LOOKUP_SEP
        )
        model = self.model

        for fieldname in fieldname_chain:
            # Throws an exception if anything goes bad
            self._validate_single_field_name(model, fieldname)
            model = self._get_related_model(model, fieldname)

        # If we get this far without an exception, everything is good
        self._CurrentSiteManager__is_validated = True

    def _validate_single_field_name(self, model, field_name):
        """Checks if the given fieldname can be used to make a link between a
        model and a site with the SpanningCurrentSiteManager class.  If
        anything is wrong, will raises an appropriate exception, because that
        is what CurrentSiteManager expects."""
        try:
            field = model._meta.get_field(field_name)
            if not isinstance(field, (models.ForeignKey,
                                      models.ManyToManyField)):
                raise TypeError(
                    "Field %r must be a ForeignKey or ManyToManyField."
                    % field_name
                )
        except FieldDoesNotExist:
            raise ValueError(
                "Couldn't find a field named %r in %s." %
                (field_name, model._meta.object_name)
            )

    def _get_related_model(self, model, fieldname):
        """Given a model and the name of a ForeignKey or ManyToManyField column
        as a string, returns the associated model."""
        return model._meta.get_field_by_name(fieldname)[0].rel.to


class PathAssistedCurrentSiteManager(models.CurrentSiteManager):
    """
    Deprecated: Use multisite.managers.SpanningCurrentSiteManager instead.
    """
    def __init__(self, field_path):
        warn(('Use multisite.managers.SpanningCurrentSiteManager instead of '
              'multisite.managers.PathAssistedCurrentSiteManager'),
             DeprecationWarning, stacklevel=2)
        super(PathAssistedCurrentSiteManager, self).__init__()
        self.__field_path = field_path

    def get_queryset(self):
        from django.contrib.sites.models import Site
        return super(models.CurrentSiteManager, self).get_queryset().filter(
                    **{self.__field_path: Site.objects.get_current()}
                )
