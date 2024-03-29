# Generated by Django 2.2.24 on 2021-06-27 19:33

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0044_account_gocardless_customer_id'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ledgeritem',
            name='type',
            field=models.CharField(choices=[('B', 'Charge'), ('C', 'Card'), ('F', 'BACS/Faster payments/SEPA'), ('E', 'SEPA Direct Debit'), ('O', 'SOFORT'), ('G', 'giropay'), ('N', 'Bancontact'), ('P', 'EPS'), ('I', 'iDEAL'), ('2', 'Przelewy24'), ('D', 'GoCardless'), ('S', 'Sources'), ('A', 'Charges'), ('H', 'Checkout'), ('M', 'Manual'), ('R', 'Stripe refund'), ('T', 'Stripe bank transfer'), ('L', 'GoCardless payment request')], default='B', max_length=1),
        ),
    ]
