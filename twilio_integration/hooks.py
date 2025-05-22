app_name = "twilio_integration"
app_title = "Twilio Integration"
app_publisher = "Frappe"
app_description = "Custom Frappe Application for Twilio Integration"
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "developers@frappe.io"
app_license = "MIT"

# app_include_css = "/assets/twilio_integration/css/twilio_call_handler.css"
# app_include_js = "/assets/twilio_integration/js/twilio_call_handler.js"

boot_session = "twilio_integration.boot.boot_session"

override_doctype_class = {
	"Notification": "twilio_integration.overrides.notification_hooks.NotificationTwilio",
	"Communication": "twilio_integration.overrides.communication_hooks.CommunicationTwilio",
}

doctype_js = {
	"Notification": "overrides/notification_hooks.js",
	# "Voice Call Settings": "public/js/voice_call_settings.js"
}

fixtures = [
	{
		"dt": "Custom Field",
		"filters": {
			"name": ["in", [
				"Notification-sec_whatsapp_template",
				"Notification-whatsapp_message_template",
				"Notification-use_whatsapp_template",
			]]
		}
	},
	{
		"dt": "Property Setter",
		"filters": {
			"name": ["in", [
				"Notification-channel-options",
				"Communication Medium-communication_medium_type-options",
			]]
		}
	}
]

scheduler_events = {
	"all": [
		"twilio_integration.twilio_integration.doctype.whatsapp_message.whatsapp_message.flush_whatsapp_message_queue",
	],
	"daily": [
		"twilio_integration.twilio_integration.doctype.whatsapp_message.whatsapp_message.expire_whatsapp_message_queue",
	],
}
