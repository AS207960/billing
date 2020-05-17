from django.urls import path
from . import views
from django.conf.urls.static import static

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('account/', views.account_details, name='account_details'),
    path('top_up/', views.top_up, name='top_up'),
    path('top_up_card/', views.top_up_card, name='top_up_card'),
    path('top_up_bacs/', views.top_up_bacs, name='top_up_bacs'),
    path('top_up_sofort/', views.top_up_sofort, name='top_up_sofort'),
    path('top_up_giropay/', views.top_up_giropay, name='top_up_giropay'),
    path('top_up_bancontact/', views.top_up_bancontact, name='top_up_bancontact'),
    path('top_up_eps/', views.top_up_eps, name='top_up_eps'),
    path('top_up_ideal/', views.top_up_ideal, name='top_up_ideal'),
    path('top_up_multibanco/', views.top_up_multibanco, name='top_up_multibanco'),
    path('top_up_p24/', views.top_up_p24, name='top_up_p24'),
    path('complete_top_up_card/<uuid:item_id>/', views.complete_top_up_card, name='complete_top_up_card'),
    path('complete_top_up_bacs/<uuid:item_id>/', views.complete_top_up_bacs, name='complete_top_up_bacs'),
    path('complete_top_up_sources/<uuid:item_id>/', views.complete_top_up_sources, name='complete_top_up_sources'),
    path('fail_top_up/<uuid:item_id>/', views.fail_top_up, name='fail_top_up'),
    path('add_card/', views.add_card, name='add_card'),
    path('edit_card/<pm_id>/', views.edit_card, name='edit_card'),
    path('stripe_webhook/', views.stripe_webhook),
    path('monzo_webhook/<secret_key>/', views.monzo_webhook),
    path('charge_user/<user_id>/', views.charge_user),
    path('subscribe_user/<user_id>/', views.subscribe_user),
    path('log_usage/<subscription_id>/', views.log_usage),
    path('save_subscription/', views.save_subscription),
    path('sw.js', views.sw)
]
