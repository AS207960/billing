self.addEventListener('push', function(event) {
    if (event.data) {
        var data = event.data.json();

        var notif = self.registration.showNotification("AS207960 Billing", {
            body: data.message,
            badge: "https://as207960.net/img/logo.png",
            icon: "https://as207960.net/img/logo.png",
            requireInteraction: true
        });

        event.waitUntil(notif);
    }
});
