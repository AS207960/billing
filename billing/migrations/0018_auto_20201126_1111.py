# Generated by Django 2.2.17 on 2020-11-26 11:11

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0017_auto_20201126_1110'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='subscription',
            name='failed_bill_attempts',
        ),
        migrations.RemoveField(
            model_name='subscription',
            name='last_bill_attempted',
        ),
    ]
