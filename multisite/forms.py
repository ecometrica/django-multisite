from __future__ import unicode_literals
from __future__ import absolute_import


from django.contrib.sites.admin import SiteAdmin
from django.core.exceptions import ValidationError

from .models import Alias


class SiteForm(SiteAdmin.form):
    def clean_domain(self):
        domain = self.cleaned_data['domain']

        try:
            alias = Alias.objects.get(domain=domain)
        except Alias.DoesNotExist:
            # New Site that doesn't clobber an Alias
            return domain

        if alias.site_id == self.instance.pk and alias.is_canonical:
            return domain

        raise ValidationError('Cannot overwrite non-canonical Alias: "%s"' %
                              alias.domain)
