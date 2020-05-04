from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('account/', views.account_details, name='account_details'),
    path('top_up/', views.top_up, name='top_up'),
    path('top_up_card/', views.top_up_card, name='top_up_card'),
    path('top_up_bacs/', views.top_up_bacs, name='top_up_bacs'),
    path('complete_top_up_card/<uuid:item_id>', views.complete_top_up_card, name='complete_top_up_card'),
    path('complete_top_up_bacs/<uuid:item_id>', views.complete_top_up_bacs, name='complete_top_up_bacs'),
    path('fail_top_up/<uuid:item_id>', views.fail_top_up, name='fail_top_up'),
    path('add_card/', views.add_card, name='add_card'),
    path('edit_card/<pm_id>/', views.edit_card, name='edit_card'),
    path('stripe_webhook/', views.stripe_webhook),
    path('monzo_webhook/<secret_key>/', views.monzo_webhook),
    path('charge_user/<user_id>/', views.charge_user),
]
