self.addEventListener('push', function(event) {
    if (event.data) {
        var data = event.data.json();

        var notif = self.registration.showNotification("Glauca Billing", {
            body: data.message,
            badge: "https://as207960.net/assets/img/logo.svg",
            icon: "https://as207960.net/assets/img/logo.svg",
            requireInteraction: true
        });

        event.waitUntil(notif);
    }
});
