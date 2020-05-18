from django import forms
import crispy_forms.helper
import crispy_forms.layout
from django_countries.fields import CountryField
from phonenumber_field.formfields import PhoneNumberField


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
        (METHOD_BACS, "BACS/Faster payments/SEPA"),
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
    method = forms.ChoiceField(choices=METHODS)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Next'))


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
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Save'))


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
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Next'))


class AccountChargeForm(forms.Form):
    amount = forms.DecimalField(decimal_places=2, max_digits=9, label="Amount (GBP)", min_value=0)
    descriptor = forms.CharField(max_length=255)
    id = forms.CharField(max_length=255, required=False, label='ID')
    can_reject = forms.BooleanField(initial=True, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Charge'))


class ManualTopUpForm(forms.Form):
    amount = forms.DecimalField(decimal_places=2, max_digits=9, label="Amount (GBP)", min_value=0)
    descriptor = forms.CharField(max_length=255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = crispy_forms.helper.FormHelper()
        self.helper.add_input(crispy_forms.layout.Submit('submit', 'Top-up'))
