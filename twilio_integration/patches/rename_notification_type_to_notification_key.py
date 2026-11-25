from frappe.patches.v15_0.rename_notification_type_to_notification_key import rename_notification_type_column


def execute():
	rename_notification_type_column("WhatsApp Message")
