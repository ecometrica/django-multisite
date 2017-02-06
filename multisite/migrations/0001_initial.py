# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import absolute_import

from django.db import models, migrations
import multisite.models


class Migration(migrations.Migration):

    dependencies = [
        ('sites', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Alias',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('domain', models.CharField(help_text='Either "domain" or "domain:port"', unique=True, max_length=100, verbose_name='domain name')),
                ('is_canonical', models.NullBooleanField(default=None, validators=[multisite.models.validate_true_or_none], editable=False, help_text='Does this domain name match the one in site?', verbose_name='is canonical?')),
                ('redirect_to_canonical', models.BooleanField(default=True, help_text='Should this domain name redirect to the one in site?', verbose_name='redirect to canonical?')),
                ('site', models.ForeignKey(related_name='aliases', to='sites.Site')),
            ],
            options={
                'verbose_name_plural': 'aliases',
            },
        ),
        migrations.AlterUniqueTogether(
            name='alias',
            unique_together=set([('is_canonical', 'site')]),
        ),
    ]
