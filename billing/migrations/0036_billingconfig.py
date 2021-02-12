# Generated by Django 2.2.17 on 2021-02-07 15:58

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0035_auto_20210203_1127'),
    ]

    operations = [
        migrations.CreateModel(
            name='BillingConfig',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('freeagent_access_token', models.TextField(blank=True, null=True)),
                ('freeagent_refresh_token', models.TextField(blank=True, null=True)),
                ('freeagent_access_token_expires_at', models.DateTimeField(blank=True, null=True)),
                ('freeagent_refresh_token_expires_at', models.DateTimeField(blank=True, null=True)),
            ],
        ),
    ]
