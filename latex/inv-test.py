import django
import pylatex
import decimal

django.setup()

import billing.models


class Letter(pylatex.base_classes.Environment):
    _latex_name = 'letter'

    def __init__(self, addressee):
        super().__init__(arguments=addressee)


class Invoice(pylatex.base_classes.Environment):
    _latex_name = 'invoice'
    packages = [pylatex.Package('invoice')]

    def __init__(self, currency, vat):
        super().__init__(arguments=(currency, vat))


class ProjectTitle(pylatex.base_classes.CommandBase):
    _latex_name = 'ProjectTitle'
    packages = [pylatex.Package('invoice')]

    def __init__(self, title):
        super().__init__(arguments=title)


class Fee(pylatex.base_classes.CommandBase):
    _latex_name = 'Fee'
    packages = [pylatex.Package('invoice')]

    def __init__(self, contents, rate, count):
        super().__init__(arguments=(contents, rate, count))


class Discount(pylatex.base_classes.CommandBase):
    _latex_name = 'Discount'
    packages = [pylatex.Package('invoice')]

    def __init__(self, contents, amount):
        super().__init__(arguments=(contents, amount))


invoice = billing.models.Invoice.objects.get(id="billing_invoice_7b156bdab36b4fa788cbe195359850be")

doc = pylatex.Document(
    documentclass='letter',
    geometry_options="paper=a4paper,top=0.8cm,bottom=0.8cm,left=0.8cm,right=0.8cm,pass"
)

doc.preamble.append(pylatex.Package("montserrat", options=("defaultfam", "tabular", "lining")))
doc.preamble.append(pylatex.Package("xltxtra"))
doc.preamble.append(pylatex.Package("xelatexemoji"))
doc.preamble.append(pylatex.Package("graphicx"))
doc.preamble.append(pylatex.Package("fancyhdr"))
doc.preamble.append(pylatex.Package("svg"))
doc.preamble.append(pylatex.Package("multicol"))

doc.preamble.append(pylatex.Command('pagestyle', arguments="fancy"))
doc.preamble.append(pylatex.Command('geometry', arguments={"headheight": "2.5cm"}))
doc.preamble.append(pylatex.NoEscape(r"\renewcommand*\oldstylenums[1]{{\fontfamily{Montserrat-TOsF}\selectfont #1}}"))
doc.preamble.append(pylatex.Command("renewcommand", arguments=[pylatex.NoEscape(r"\headrulewidth"), "0pt"]))
doc.preamble.append(pylatex.Command("addtolength", arguments=[pylatex.NoEscape(r"\oddsidemargin"), "-4cm"]))
doc.preamble.append(pylatex.Command("addtolength", arguments=[pylatex.NoEscape(r"\evensidemargin"), "-4cm"]))
doc.preamble.append(pylatex.Command("addtolength", arguments=[pylatex.NoEscape(r"\topmargin"), "-2.5cm"]))
doc.preamble.append(pylatex.Command("addtolength", arguments=[pylatex.NoEscape(r"\headsep"), "-1.5cm"]))
doc.preamble.append(pylatex.Command("addtolength", arguments=[pylatex.NoEscape(r"\textwidth"), "8cm"]))
doc.preamble.append(pylatex.Command("addtolength", arguments=[pylatex.NoEscape(r"\textheight"), "3cm"]))
doc.preamble.append(pylatex.NoEscape(r"""\fancypagestyle{empty}{\fancyhf{}\fancyhead[L]{
  \raisebox{-.4\height}{\includesvg[height=2.5cm, keepaspectratio=true]{latex/logo}}
  \hspace{1cm}
  \huge{\textbf{AS207960 / Glauca}}
}}"""))
doc.preamble.append(pylatex.NoEscape(r"\renewcommand{\xelatexemojipath}[1]{latex/svg/emoji_u#1.pdf}"))
doc.preamble.append(pylatex.Command(
    'address',
    arguments=pylatex.NoEscape("AS207960 Cyfyngedig\\\\13 Pen-y-lan Terrace\\\\Caerdydd\\\\Cymru\\\\CF23 9EU\\\\GB")
))

with doc.create(Letter(f"""{invoice.account.user.first_name} {invoice.account.user.last_name}
{invoice.account.user.email}
Account ID: {invoice.account.user.username}""")):
    doc.append(pylatex.Command("date", arguments=invoice.invoice_date.strftime("%a %d %B %Y")))
    doc.append(pylatex.Command("opening", arguments=f"Invoice {invoice.ref}"))

    with doc.create(Invoice("GBP", 0)):
        doc.append(ProjectTitle(invoice.description))

        for fee in invoice.invoicefee_set.all():
            doc.append(Fee(
                fee.descriptor,
                '{:f}'.format(fee.units.normalize()),
                '{:f}'.format(fee.rate_per_unit.normalize())
            ))

        for discount in invoice.invoicediscount_set.all():
            doc.append(Discount(discount.descriptor, '{:f}'.format(discount.amount.normalize())))

    doc.append(f"Due: {invoice.due_date.strftime('%a %d %B %Y')}")
    doc.append("\n")
    doc.append("Payment via:")
    doc.append(pylatex.NoEscape(r"""
\begin{multicols}{2}
\textbf{UK BACS/Faster Payments}\\
Sort Code: \textbf{04-00-04}\\
Account Number: \textbf{53868700}\\
Account holder: \textbf{AS207960 Cyfyngedig}

\columnbreak

\textbf{International (any currency acceptted)}\\
Account holder: \textbf{AS207960 Cyfyngedig}\\
IBAN: \textbf{GB06MONZ04000453868700}\\
BIC: \textbf{MONZGB2L}
\end{multicols}
Note: You are responsible for any international transfer fees.\\
Bank address: Monzo Bank Limited, Broadwalk House, 5 Appold St, London, EC2A 2DA, GB"""))

doc.generate_pdf(f"invoice_{invoice.ref}", compiler="xelatex", compiler_args=["--shell-escape"])
