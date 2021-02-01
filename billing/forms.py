import crispy_forms.helper
import crispy_forms.layout
import django.core.exceptions
import zeep.exceptions
import decimal
from django import forms
from django.conf import settings
from django_countries.fields import CountryField
from phonenumber_field.formfields import PhoneNumberField

from . import models, apps, vat, utils


class VATMOSSForm(forms.Form):
    QUARTERS = (
        (1, "Q1 (1st January - 31st March)"),
        (2, "Q2 (1st April - 30th June)"),
        (3, "Q3 (1st July - 30th September)"),
        (4, "Q4 (1st October - 31st December)"),
    )

    year = forms.IntegerField(min_value=0)
    quarter = forms.TypedChoiceField(choices=QUARTERS, widget=forms.RadioSelect(), coerce=int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.field_class = 'my-2'
        self.helper.layout = crispy_forms.layout.Layout(
            'year',
            'quarter'
        )
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Export', css_class='w-100'))


class TopUpForm(forms.Form):
    METHOD_CARD = 'C'
    METHOD_BACS = 'B'
    METHOD_SOFORT = 'S'
    METHOD_GIROPAY = 'G'
    METHOD_BANCONTACT = 'A'
    METHOD_EPS = 'E'
    METHOD_IDEAL = 'I'
    METHOD_MULTIBANCO = 'M'
    METHOD_P24 = 'P'
    METHODS = (
        (METHOD_BACS, "Bank Transfer (US, SG, RO, NZ, HU, EU, AU, GB) / SWIFT (Anywhere)"),
        (METHOD_SOFORT, "SOFORT"),
        (METHOD_GIROPAY, "giropay"),
        (METHOD_BANCONTACT, "Bancontact"),
        (METHOD_EPS, "EPS"),
        (METHOD_IDEAL, "iDEAL"),
        (METHOD_MULTIBANCO, "Multibanco"),
        (METHOD_P24, "Przelewy24"),
        (METHOD_CARD, "Card"),
    )

    amount = forms.DecimalField(decimal_places=2, max_digits=9, label="Amount (GBP)", min_value=2)
    method = forms.ChoiceField(choices=METHODS, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Next', css_class='btn-block'))


class TopUpRefundForm(forms.Form):
    amount = forms.DecimalField(decimal_places=2, max_digits=9, label="Amount (GBP)", min_value=decimal.Decimal('0.01'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class CompleteChargeForm(forms.Form):
    METHOD_CARD = 'C'
    METHOD_GIROPAY = 'G'
    METHOD_BANCONTACT = 'A'
    METHOD_EPS = 'E'
    METHOD_IDEAL = 'I'
    METHOD_P24 = 'P'
    METHODS = (
        (METHOD_GIROPAY, "giropay"),
        (METHOD_BANCONTACT, "Bancontact"),
        (METHOD_EPS, "EPS"),
        (METHOD_IDEAL, "iDEAL"),
        (METHOD_P24, "Przelewy24"),
        (METHOD_CARD, "Card"),
    )

    method = forms.ChoiceField(choices=METHODS)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Next', css_class='btn-block'))


class EditCardForm(forms.Form):
    name = forms.CharField(required=False)
    email = forms.CharField(required=False)
    phone = PhoneNumberField(required=False)
    address_line1 = forms.CharField(required=False, label="Address line 1")
    address_line2 = forms.CharField(required=False, label="Address line 2")
    address_city = forms.CharField(required=False, label="City")
    address_state = forms.CharField(required=False, label="State")
    address_postal_code = forms.CharField(required=False, label="Postal code")
    address_country = CountryField().formfield(required=False, label="Country")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Hidden("action", "edit"))
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Save', css_class='btn-block'))


class BillingAddressForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance.vat_id:
            self.fields['vat_id'].disabled = True
        if not self.instance._state.adding:
            self.fields['country_code'].disabled = True

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Save', css_class='btn-block'))

    class Meta:
        model = models.AccountBillingAddress
        exclude = ('id', 'account', 'deleted', 'default', 'vat_id_verification_request')

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)
        country_code = self.cleaned_data['country_code']
        if country_code == "CA":
            postal_code_match = utils.canada_postcode_re.fullmatch(self.cleaned_data['postal_code'])
            if not postal_code_match:
                raise django.core.exceptions.ValidationError({
                    'postal_code': ["Invalid postal code format for Canada"]
                })
        if country_code == "GB":
            postal_code_match = utils.uk_postcode_re.fullmatch(self.cleaned_data['postal_code'])
            if not postal_code_match:
                raise django.core.exceptions.ValidationError({
                    'postal_code': ["Invalid postal code format for the UK"]
                })
        if country_code == "ES":
            postal_code_match = utils.spain_postcode_re.fullmatch(self.cleaned_data['postal_code'])
            if not postal_code_match:
                raise django.core.exceptions.ValidationError({
                    'postal_code': ["Invalid postal code format for Spain"]
                })
        if country_code == "DE":
            postal_code_match = utils.germany_postcode_re.fullmatch(self.cleaned_data['postal_code'])
            if not postal_code_match:
                raise django.core.exceptions.ValidationError({
                    'postal_code': ["Invalid postal code format for Germany"]
                })
        if country_code == "FR":
            postal_code_match = utils.france_postcode_re.fullmatch(self.cleaned_data['postal_code'])
            if not postal_code_match:
                raise django.core.exceptions.ValidationError({
                    'postal_code': ["Invalid postal code format for France"]
                })
        if self.cleaned_data['vat_id']:
            if country_code == "GB":
                vat_lookup_state, vat_lookup_data = vat.verify_vat_hmrc(self.cleaned_data['vat_id'])
                if vat_lookup_state == vat.VerifyVATStatus.ERROR:
                    raise django.core.exceptions.ValidationError({
                        django.core.exceptions.NON_FIELD_ERRORS: [
                            "VAT check service currently unavailable, please try again later"
                        ]
                    })
                elif vat_lookup_state == vat.VerifyVATStatus.INVALID:
                    raise django.core.exceptions.ValidationError({
                        'vat_id': ["Invalid VAT ID"]
                    })
                elif vat_lookup_data:
                    self.cleaned_data['organisation'] = vat_lookup_data.name
                    self.cleaned_data['street_1'] = vat_lookup_data.address_line1
                    if vat_lookup_data.address_line2:
                        self.cleaned_data['street_2'] = vat_lookup_data.address_line2
                    if vat_lookup_data.address_line3:
                        self.cleaned_data['street_3'] = vat_lookup_data.address_line3
                    if vat_lookup_data.address_line4:
                        self.cleaned_data['city'] = vat_lookup_data.address_line4
                    if vat_lookup_data.address_line5:
                        self.cleaned_data['province'] = vat_lookup_data.address_line5
                    if vat_lookup_data.post_code:
                        self.cleaned_data['postal_code'] = vat_lookup_data.post_code
                    self.instance.vat_id_verification_request = vat_lookup_data.consultation_number
            else:
                vies_country = vat.get_vies_country_code(country_code)
                if vies_country:
                    try:
                        vat_resp = apps.vies_client.service.checkVatApprox(
                            countryCode=vies_country,
                            vatNumber=self.cleaned_data['vat_id'],
                            traderName=self.cleaned_data['organisation'],
                            requesterCountryCode=settings.OWN_EU_VAT_COUNTRY,
                            requesterVatNumber=settings.OWN_EU_VAT_ID,
                        )
                    except zeep.exceptions.Fault as e:
                        if e.message in ("SERVICE_UNAVAILABLE", "MS_UNAVAILABLE"):
                            raise django.core.exceptions.ValidationError({
                                django.core.exceptions.NON_FIELD_ERRORS: [
                                    "VAT check service currently unavailable, please try again later"
                                ]
                            })
                        else:
                            raise django.core.exceptions.ValidationError({
                                django.core.exceptions.NON_FIELD_ERRORS: [
                                    "An unexpected error occurred"
                                ]
                            })
                    if not vat_resp["valid"]:
                        raise django.core.exceptions.ValidationError({
                            'vat_id': ["Invalid VAT ID"]
                        })
                    if vat_resp["traderName"] is not None:
                        self.cleaned_data['organisation'] = vat_resp["traderName"]
                    if vat_resp["traderPostcode"] is not None:
                        self.cleaned_data['postal_code'] = vat_resp["traderPostcode"]
                    if vat_resp["traderCity"] is not None:
                        self.cleaned_data['city'] = vat_resp["traderCity"]
                    self.instance.vat_id_verification_request = vat_resp["requestIdentifier"]


class SOFORTForm(forms.Form):
    COUNTRIES = (
        ("AT", "Austria"),
        ("BE", "Belgium"),
        ("DE", "Germany"),
        ("IT", "Italy"),
        ("NL", "Netherlands"),
        ("ES", "Spain")
    )

    account_country = forms.ChoiceField(choices=COUNTRIES)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Next', css_class='btn-block'))


class StatementExportForm(forms.Form):
    FORMAT_CSV = "C"
    FORMAT_QIF = "Q"
    FORMAT_PDF = "P"
    FORMATS = (
        (FORMAT_CSV, "CSV"),
        (FORMAT_QIF, "QIF"),
        (FORMAT_PDF, "PDF"),
    )

    date_from = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    date_to = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    format = forms.ChoiceField(choices=FORMATS, widget=forms.RadioSelect())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.field_class = 'my-2'
        self.helper.layout = crispy_forms.layout.Layout(
            crispy_forms.layout.Div(
                crispy_forms.layout.Div('date_from', css_class='col-sm-6'),
                crispy_forms.layout.Div('date_to', css_class='col-sm-6'),
                css_class='row'
            ),
            'format'
        )
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Export', css_class='w-100'))


class AccountChargeForm(forms.Form):
    amount = forms.DecimalField(decimal_places=2, max_digits=9, label="Amount (GBP)", min_value=0)
    descriptor = forms.CharField(max_length=255)
    id = forms.CharField(max_length=255, required=False, label='ID')
    can_reject = forms.BooleanField(initial=True, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Charge', css_class='btn-block'))


class ManualTopUpForm(forms.Form):
    amount = forms.DecimalField(decimal_places=2, max_digits=9, label="Amount (GBP)", min_value=0)
    descriptor = forms.CharField(max_length=255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Top-up', css_class='btn-block'))


class BACSMarkPaidForm(forms.Form):
    amount = forms.DecimalField(decimal_places=2, max_digits=9, label="Final amount", min_value=0)
    currency = forms.ChoiceField(choices=(
        ("gbp", "Pound Sterling"),
        ("eur", "Euro"),
        ("usd", "United States Dollar"),
        ("cad", "Canadian Dollar"),
        ("dkk", "Danish Krona"),
        ("sek", "Swedish Krona"),
        ("aud", "Australian Dollar"),
        ("nzd", "New Zealand Dollar"),
        ("huf", "Hungarian Florint"),
        ("ron", "Romanian Leu"),
        ("sgd", "Singapore Dollar"),
        ("try", "Turkish Lira")
    ))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Mark paid', css_class='btn-block'))


class GBBankAccountForm(forms.Form):
    branch_code = forms.CharField(max_length=6, min_length=6, label="Sort code")
    account_number = forms.CharField(max_length=8, min_length=6, label="Account number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class AUBankAccountForm(forms.Form):
    branch_code = forms.CharField(max_length=6, min_length=6, label="BSB Number")
    account_number = forms.CharField(max_length=9, min_length=5, label="Account number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class ATBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=5, min_length=5, label="Bankleitzahl")
    account_number = forms.CharField(max_length=11, min_length=4, label="Kontonummer")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class BEBankAccountForm(forms.Form):
    account_number = forms.CharField(min_length=4, label="Rekeningnummer/Numéro de compte")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class CABankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=3, min_length=2, label="Financial Institution number")
    branch_code = forms.CharField(max_length=5, min_length=5, label="Branch Transit number")
    account_number = forms.CharField(max_length=12, min_length=7, label="Account number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class CYBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=3, min_length=3, label="Kodikos Trapezas")
    branch_code = forms.CharField(max_length=5, min_length=5, label="Kodikos Katastimatos")
    account_number = forms.CharField(max_length=16, min_length=7, label="Arithmos Logariasmou")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class DKBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=4, min_length=2, label="Registreringsnummer")
    account_number = forms.CharField(max_length=10, min_length=9, label="Kontonumme")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class EEBankAccountForm(forms.Form):
    account_number = forms.CharField(max_length=14, label="Account number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class FIBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=6, min_length=6, label="Bank code")
    account_number = forms.CharField(max_length=8, min_length=1, label="Account number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class FRBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=5, min_length=5, label="Code banque")
    branch_code = forms.CharField(max_length=5, min_length=5, label="Code guiche")
    account_number = forms.CharField(max_length=13, min_length=3, label="Numéro de compte")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class DEBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=8, min_length=8, label="Bankleitzahl")
    account_number = forms.CharField(max_length=10, min_length=1, label="Kontonummer")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class GRBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=3, min_length=3, label="Kodikos Trapezas")
    branch_code = forms.CharField(max_length=5, min_length=5, label="Kodikos Katastimatos")
    account_number = forms.CharField(max_length=16, min_length=16, label="Arithmos Logariasmou")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class IEBankAccountForm(forms.Form):
    branch_code = forms.CharField(max_length=6, min_length=6, label="Sort code")
    account_number = forms.CharField(max_length=8, min_length=6, label="Account number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class ITBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=5, min_length=5, label="Codice ABI")
    branch_code = forms.CharField(max_length=5, min_length=5, label="CAB")
    account_number = forms.CharField(max_length=12, label="Numero di conto")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class LVBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=4, min_length=4, label="Bank code")
    account_number = forms.CharField(max_length=13, min_length=13, label="Account number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class LTBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=5, min_length=5, label="Bank code")
    account_number = forms.CharField(max_length=11, min_length=11, label="Account number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class LUBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=3, min_length=3, label="Bank code")
    account_number = forms.CharField(max_length=13, min_length=13, label="Account number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class MTBankAccountForm(forms.Form):
    branch_code = forms.CharField(max_length=5, min_length=5, label="Sort code")
    account_number = forms.CharField(max_length=18, label="Account number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class MCBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=5, min_length=5, label="Code banque")
    branch_code = forms.CharField(max_length=5, min_length=5, label="Code guichet")
    account_number = forms.CharField(max_length=13, min_length=3, label="Numéro de compte")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class NLBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=4, min_length=4, label="Bank code")
    account_number = forms.CharField(max_length=10, label="Rekeningnummer")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class NZBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=2, min_length=2, label="Bank number")
    branch_code = forms.CharField(max_length=4, min_length=3, label="Branch number")
    account_number = forms.CharField(max_length=11, min_length=9, label="Account number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class PTBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=4, min_length=4, label="Código de Banco")
    branch_code = forms.CharField(max_length=4, min_length=4, label="Código de Balcão")
    account_number = forms.CharField(max_length=13, min_length=13, label="Número de conta")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class SMBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=5, min_length=5, label="Codice ABI")
    branch_code = forms.CharField(max_length=5, min_length=5, label="CAB")
    account_number = forms.CharField(max_length=12, label="Numero di conto")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class SKBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=4, min_length=4, label="Kód banky")
    account_number = forms.CharField(max_length=14, label="Předčíslí / Číslo účtu")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class SIBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=5, min_length=5, label="Bank code")
    account_number = forms.CharField(max_length=10, label="Account number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class ESBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=4, min_length=4, label="Código de entidad")
    branch_code = forms.CharField(max_length=4, min_length=4, label="Código de oficina")
    account_number = forms.CharField(max_length=12, min_length=12, label="Dígitos de control / número de cuenta")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class SEBankAccountForm(forms.Form):
    branch_code = forms.CharField(max_length=5, min_length=4, label="Clearingnummer")
    account_number = forms.CharField(max_length=10, label="Kontonummer")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))


class USBankAccountForm(forms.Form):
    bank_code = forms.CharField(max_length=9, min_length=9, label="Routing number")
    account_number = forms.CharField(max_length=17, label="Account number")
    account_type = forms.ChoiceField(choices=(
        ("checking", "Checking"),
        ("savings", "Savings"),
    ))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Submit', css_class='btn-block'))
