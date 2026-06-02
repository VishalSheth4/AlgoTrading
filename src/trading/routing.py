from django.urls import re_path
from trading import consumers

websocket_urlpatterns = [
    re_path(r"ws/price/(?P<symbol>[^/]+)/$", consumers.PriceConsumer.as_asgi()),
    re_path(r"ws/trades/$",                   consumers.TradesConsumer.as_asgi()),
]
