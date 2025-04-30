import frappe
from frappe import _
from twilio_integration.twilio_integration.doctype.whatsapp_message.whatsapp_message import WhatsAppMessage
from frappe.core.doctype.notification_count.notification_count import set_notification_last_scheduled
from frappe.email.doctype.notification.notification import (
    Notification,
    get_context,
    json,
    get_reference_doctype,
    get_reference_name,
)

class SendNotification(Notification):
	def validate(self):
		super().validate()
		self.validate_twilio_settings()

	def validate_twilio_settings(self):
		if self.enabled and self.channel == "WhatsApp":
			twilio_settings = frappe.get_single("Twilio Settings")
			if not twilio_settings.enabled:
				frappe.throw(_("Twilio Settings must be enabled to send WhatsApp notifications."))
			if not twilio_settings.whatsapp_no:
				frappe.throw(_("Twilio WhatsApp Number is required in Twilio Settings."))

	def send(self, doc, context=None):

		if not context:
			context = {}

		context.update({"doc": doc, "alert": self, "comments": None})

		if doc.get("_comments"):
			context["comments"] = json.loads(doc.get("_comments"))

		if self.is_standard:
			self.load_standard_properties(context)

		ref_doctype = get_reference_doctype(doc)
		ref_name = get_reference_name(doc)

		try:
			if self.channel == 'WhatsApp':
				self.send_whatsapp_msg(doc, ref_doctype, ref_name, context)
		except Exception as e:
			frappe.log_error(
				message=frappe.get_traceback(),
				title=_("Failed to send WhatsApp Notification: {0} for {1} {2}").format(self.name, ref_doctype, ref_name)
			)

		super(SendNotification, self).send(doc,context=context)

	def send_whatsapp_msg(self, doc, ref_doctype, ref_name, context):
		try:
			notification_type = self.get_notification_type()
			receiver_list = self.get_receiver_list(doc, context)

			if not receiver_list:
				return

			formatted_receiver_list = self.format_numbers_for_whatsapp(receiver_list)

			if not formatted_receiver_list:
				return

			message_content = frappe.render_template(self.message, context)

			self.create_communication_for_whatsapp(doc, message=message_content, receiver_list= formatted_receiver_list)

			if notification_type:
				set_notification_last_scheduled(ref_doctype, ref_name, notification_type, "WhatsApp")

			WhatsAppMessage.send_whatsapp_message(
				receiver_list=formatted_receiver_list,
				message=message_content,
				doctype = ref_doctype,
				docname = ref_name,
				notification_type=notification_type
			)
		except Exception as e:
			frappe.log_error(
				message=frappe.get_traceback(),
				title=_("Failed to send WhatsApp Notification: {0}").format(self.name)
			)

	def create_communication_for_whatsapp(self, doc, message=None, receiver_list=None):
		try:
			timeline_doctype, timeline_name = self.get_timeline_doctype_and_name(doc)

			communication = frappe.get_doc({
				"doctype": "Communication",
				"communication_type": "Automated Message",
				"communication_medium": "Other",
				"subject": f"WhatsApp: {doc.name}",
				"content": message,
				"sent_or_received": "Sent",
				"reference_doctype": get_reference_doctype(doc),
				"reference_name": get_reference_name(doc),
				"sender": frappe.session.user,
				"recipients": "\n".join(receiver_list),
				"phone_no": receiver_list[0] if len(receiver_list) == 1 else None
			})

			if timeline_doctype and timeline_name:
				communication.append("timeline_links", {
					"link_doctype": timeline_doctype,
					"link_name": timeline_name
				})

			communication.insert(ignore_permissions=True)

		except Exception as e:
			# Log error but don't necessarily stop the WhatsApp send itself
			frappe.log_error(
				message=frappe.get_traceback(),
				title=_("Failed to create Communication for WhatsApp Notification: {0}").format(self.name)
			)

	def format_numbers_for_whatsapp(self, receiver_list):
		"""Format phone numbers to international format"""
		from frappe.regional.regional import local_to_international_mobile_no
		from frappe.core.doctype.sms_settings.sms_settings import clean_receiver_nos

		formatted_list = []
		cleaned_receiver_list = clean_receiver_nos(receiver_list)

		for number in cleaned_receiver_list:
			if not number:
				continue

			clean_number = local_to_international_mobile_no(number)
			formatted_list.append(clean_number)

		return formatted_list
