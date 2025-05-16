import frappe
from collections import Counter
from frappe.core.doctype.communication.communication import Communication


class CommunicationTwilio(Communication):
	def set_delivery_status(self, commit=False):
		"""Look into the status of WhatsApp Queue linked to this Communication and set the Delivery Status of this Communication"""
		delivery_status = None

		if self.communication_medium != "WhatsApp":
			super().set_delivery_status()
			return

		status_counts = Counter(
			frappe.db.sql_list('''select status from `tabWhatsApp Message` where communication=%s''', self.name))

		if self.sent_or_received == "Received":
			return

		if status_counts.get("Queued") or status_counts.get("Not Sent") or status_counts.get("Sending"):
			delivery_status = "Sending"
		elif status_counts.get("Undelivered") or status_counts.get("Error"):
			delivery_status = "Error"
		elif status_counts.get("Sent") or status_counts.get("Delivered") or status_counts.get("Read"):
			delivery_status = "Sent"

		if delivery_status:
			self.db_set("delivery_status", delivery_status)
			self.notify_change("update")
			self.notify_update()

			if commit:
				frappe.db.commit()
