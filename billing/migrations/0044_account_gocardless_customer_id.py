# Generated by Django 2.2.24 on 2021-06-27 17:28

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0043_chargestate_amount'),
    ]

    operations = [
        migrations.AddField(
            model_name='account',
            name='gocardless_customer_id',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]