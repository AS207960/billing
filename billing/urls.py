from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('account/', views.account_details, name='account_details'),
    path('account/export/', views.statement_export, name='statement_export'),
    path('top_up/', views.top_up, name='top_up'),
    path('top_up_card/', views.top_up_card, name='top_up_card'),
    path('top_up_existing_card/<str:card_id>', views.top_up_existing_card, name='top_up_existing_card'),
    path('top_up_bacs/', views.top_up_bacs, name='top_up_bacs'),
    path('top_up_bacs/<str:country>/', views.top_up_bacs_local, name='top_up_bacs_local'),
    path('top_up_bank_details/<str:currency>/', views.top_up_bank_details, name='top_up_bank_details'),
    path('top_up_existing_ach_direct_debit/<str:mandate_id>/', views.top_up_existing_ach_direct_debit,
         name='top_up_existing_ach_direct_debit'),
    path('top_up_existing_autogiro_direct_debit/<str:mandate_id>/', views.top_up_existing_autogiro_direct_debit,
         name='top_up_existing_autogiro_direct_debit'),
    path('top_up_existing_bacs_direct_debit/<str:mandate_id>/', views.top_up_existing_bacs_direct_debit,
         name='top_up_existing_bacs_direct_debit'),
    path('top_up_existing_becs_direct_debit/<str:mandate_id>/', views.top_up_existing_becs_direct_debit,
         name='top_up_existing_becs_direct_debit'),
    path('top_up_existing_becs_nz_direct_debit/<str:mandate_id>/', views.top_up_existing_becs_nz_direct_debit,
         name='top_up_existing_becs_nz_direct_debit'),
    path('top_up_existing_betalingsservice_direct_debit/<str:mandate_id>/',
         views.top_up_existing_betalingsservice_direct_debit,
         name='top_up_existing_betalingsservice_direct_debit'),
    path('top_up_existing_pad_direct_debit/<str:mandate_id>/', views.top_up_existing_pad_direct_debit,
         name='top_up_existing_pad_direct_debit'),
    path('top_up_existing_sepa_direct_debit/<str:mandate_id>/', views.top_up_existing_sepa_direct_debit,
         name='top_up_existing_sepa_direct_debit'),
    path('top_up_sofort/', views.top_up_sofort, name='top_up_sofort'),
    path('top_up_giropay/', views.top_up_giropay, name='top_up_giropay'),
    path('top_up_bancontact/', views.top_up_bancontact, name='top_up_bancontact'),
    path('top_up_eps/', views.top_up_eps, name='top_up_eps'),
    path('top_up_ideal/', views.top_up_ideal, name='top_up_ideal'),
    path('top_up_multibanco/', views.top_up_multibanco, name='top_up_multibanco'),
    path('top_up_p24/', views.top_up_p24, name='top_up_p24'),
    path('top_up_new_ach/', views.top_up_new_ach, name='top_up_new_ach'),
    path('top_up_new_ach_complete/', views.top_up_new_ach_complete, name='top_up_new_ach_complete'),
    path('top_up_new_autogiro/', views.top_up_new_autogiro, name='top_up_new_autogiro'),
    path('top_up_new_autogiro_complete/', views.top_up_new_autogiro_complete, name='top_up_new_autogiro_complete'),
    path('top_up_new_bacs/', views.top_up_new_bacs, name='top_up_new_bacs'),
    path('top_up_new_bacs_complete/', views.top_up_new_bacs_complete, name='top_up_new_bacs_complete'),
    path('top_up_new_becs/', views.top_up_new_becs, name='top_up_new_becs'),
    path('top_up_new_becs_complete/', views.top_up_new_becs_complete, name='top_up_new_becs_complete'),
    path('top_up_new_becs_nz/', views.top_up_new_becs_nz, name='top_up_new_becs_nz'),
    path('top_up_new_becs_nz_complete/', views.top_up_new_becs_nz_complete, name='top_up_new_becs_nz_complete'),
    path('top_up_new_betalingsservice/', views.top_up_new_betalingsservice, name='top_up_new_betalingsservice'),
    path('top_up_new_betalingsservice_complete/', views.top_up_new_betalingsservice_complete,
         name='top_up_new_betalingsservice_complete'),
    path('top_up_new_pad/', views.top_up_new_pad, name='top_up_new_pad'),
    path('top_up_new_pod_complete/', views.top_up_new_pad_complete, name='top_up_new_pad_complete'),
    path('top_up_new_sepa/', views.top_up_new_sepa, name='top_up_new_sepa'),
    path('top_up_new_sepa_complete/', views.top_up_new_sepa_complete, name='top_up_new_sepa_complete'),
    path('complete_top_up_card/<str:item_id>/', views.complete_top_up_card, name='complete_top_up_card'),
    path('complete_top_up_bacs/<str:item_id>/', views.complete_top_up_bacs, name='complete_top_up_bacs'),
    path('complete_top_up_bacs/<str:item_id>/<str:currency>/', views.complete_top_up_bank_details,
         name='complete_top_up_bank_details'),
    path('complete_top_up_sepa_direct_debit/<str:item_id>/', views.complete_top_up_sepa_direct_debit,
         name='complete_top_up_sepa_direct_debit'),
    path('complete_top_up_checkout/<str:item_id>/', views.complete_top_up_checkout, name='complete_top_up_checkout'),
    path('complete_top_up_sources/<str:item_id>/', views.complete_top_up_sources, name='complete_top_up_sources'),
    path('fail_top_up/<str:item_id>/', views.fail_top_up, name='fail_top_up'),
    path('complete_charge/<str:charge_id>/', views.complete_charge, name='complete_charge'),
    path('fail_charge/<str:charge_id>/', views.fail_charge, name='fail_charge'),
    path('add_card/', views.add_card, name='add_card'),
    path('edit_card/<pm_id>/', views.edit_card, name='edit_card'),
    path('view_ach_mandate/<str:m_id>/', views.view_ach_mandate, name='view_ach_mandate'),
    path('edit_ach_mandate/<str:m_id>/', views.edit_ach_mandate, name='edit_ach_mandate'),
    path('view_autogiro_mandate/<str:m_id>/', views.view_autogiro_mandate, name='view_autogiro_mandate'),
    path('edit_autogiro_mandate/<str:m_id>/', views.edit_autogiro_mandate, name='edit_autogiro_mandate'),
    path('view_bacs_mandate/<str:m_id>/', views.view_bacs_mandate, name='view_bacs_mandate'),
    path('edit_bacs_mandate/<str:m_id>/', views.edit_bacs_mandate, name='edit_bacs_mandate'),
    path('view_becs_mandate/<str:m_id>/', views.view_becs_mandate, name='view_becs_mandate'),
    path('edit_becs_mandate/<str:m_id>/', views.edit_becs_mandate, name='edit_becs_mandate'),
    path('view_becs_nz_mandate/<str:m_id>/', views.view_becs_nz_mandate, name='view_becs_nz_mandate'),
    path('edit_becs_nz_mandate/<str:m_id>/', views.edit_becs_nz_mandate, name='edit_becs_nz_mandate'),
    path('view_betalingsservice_mandate/<str:m_id>/', views.view_betalingsservice_mandate, name='view_betalingsservice_mandate'),
    path('edit_betalingsservice_mandate/<str:m_id>/', views.edit_betalingsservice_mandate, name='edit_betalingsservice_mandate'),
    path('view_pad_mandate/<str:m_id>/', views.view_pad_mandate, name='view_pad_mandate'),
    path('edit_pad_mandate/<str:m_id>/', views.edit_pad_mandate, name='edit_pad_mandate'),
    path('view_sepa_mandate/<str:m_id>/', views.view_sepa_mandate, name='view_sepa_mandate'),
    path('edit_sepa_mandate/<str:m_id>/', views.edit_sepa_mandate, name='edit_sepa_mandate'),
    path('edit_subscription/<str:s_id>/', views.edit_subscription, name='edit_subscription'),
    path('stripe_webhook/', views.stripe_webhook),
    path('gc_webhook/', views.gc_webhook),
    path('xfw_webhook/', views.xfw_webhook),
    path('monzo_webhook/<secret_key>/', views.monzo_webhook),
    path('charge_user/<user_id>/', views.charge_user),
    path('get_charge_state/<str:charge_state_id>/', views.get_charge_state),
    path('reverse_charge/', views.reverse_charge),
    path('subscribe_user/<user_id>/', views.subscribe_user),
    path('log_usage/<subscription_id>/', views.log_usage),
    path('convert_currency/', views.convert_currency),
    path('save_subscription/', views.save_subscription),
    path('sw.js', views.sw),
    path('accounts/', views.view_accounts, name='view_accounts'),
    path('accounts/<account_id>/', views.view_account, name='view_account'),
    path('accounts/edit_item/<item_id>/', views.edit_ledger_item, name='edit_ledger_item'),
    path('accounts/<account_id>/charge/', views.charge_account, name='charge_account'),
    path('accounts/<account_id>/top_up/', views.manual_top_up_account, name='manual_top_up_account'),
]
