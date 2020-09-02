self.addEventListener('push', function(event) {
    if (event.data) {
        var data = event.data.json();

        var notif = self.registration.showNotification("Glauca Billing", {
            body: data.message,
            badge: "https://as207960.net/assets/img/logo2.png",
            icon: "https://as207960.net/assets/img/logo2.png",
            requireInteraction: true
        });

        event.waitUntil(notif);
    }
});
