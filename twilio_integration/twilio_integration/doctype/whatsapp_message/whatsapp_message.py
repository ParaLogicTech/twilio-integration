# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.password import get_decrypted_password
from frappe.utils import get_site_url
from ...twilio_handler import Twilio
import json


class WhatsAppMessage(Document):
	def send(self):
		client = Twilio.get_twilio_client()
		message_dict = self.get_message_dict()
		response = frappe._dict()

		try:
			response = client.messages.create(**message_dict)
			self.sent_received = 'Sent'
			self.status = response.status.title()
			self.id = response.sid
			self.send_on = response.date_sent
			self.save(ignore_permissions=True)

		except Exception as e:
			self.db_set('status', "Error")
			self.log_error(title=_('Twilio WhatsApp Message Error'), message=e)

	def get_message_dict(self):
		args = {
			'from_': self.from_,
			'to': self.to,
			'status_callback': '{}/api/method/twilio_integration.twilio_integration.api.whatsapp_message_status_callback'.format(
				get_site_url(frappe.local.site))
		}

		if self.template_sid:
			args['content_sid'] = self.template_sid
			if self.content_variables:
				args['content_variables'] = self.content_variables
		else:
			args['body'] = self.message

		if self.media_link:
			args['media_url'] = [self.media_link]

		return args

	@classmethod
	def send_whatsapp_message(
		cls,
		receiver_list,
		message=None,
		doctype=None,
		docname=None,
		notification_type=None,
		media=None,
		communication=None,
		template_sid=None,
		content_variables=None,
	):
		from frappe.email.doctype.notification.notification import get_doc_for_notification_triggers

		if are_whatsapp_messages_muted():
			frappe.msgprint(_("WhatsApp is muted"))
			return

		if isinstance(receiver_list, str):
			receiver_list = json.loads(receiver_list)
			if not isinstance(receiver_list, list):
				receiver_list = [receiver_list]

		doc = get_doc_for_notification_triggers(doctype, docname)

		run_before_send_method(doc=doc, notification_type=notification_type)

		for rec in receiver_list:
			wa_msg = cls.store_whatsapp_message(
				to=rec,
				message=message,
				doctype=doctype,
				docname=docname,
				media=media,
				communication=communication,
				template_sid=template_sid,
				content_variables=content_variables
			)
			wa_msg.send()

		run_after_send_method(doctype=doctype, docname=docname, notification_type=notification_type)

	@classmethod
	def store_whatsapp_message(
		cls,
		to,
		message=None,
		doctype=None,
		docname=None,
		media=None,
		communication=None,
		template_sid=None,
		content_variables=None,
	):
		sender = frappe.db.get_single_value('Twilio Settings', 'whatsapp_no')

		wa_msg = frappe.get_doc({
			'doctype': 'WhatsApp Message',
			'from_': f'whatsapp:{sender}',
			'to': f'whatsapp:{to}',
			'message': message,
			'reference_doctype': doctype,
			'reference_document_name': docname,
			'media_link': media,
			'communication': communication,
			'template_sid': template_sid,
			'content_variables': content_variables
		}).insert(ignore_permissions=True)

		return wa_msg


def incoming_message_callback(args):
	wa_msg = frappe.get_doc({
		'doctype': 'WhatsApp Message',
		'from_': args.From,
		'to': args.To,
		'message': args.Body,
		'profile_name': args.ProfileName,
		'sent_received': args.SmsStatus.title(),
		'id': args.MessageSid,
		'send_on': frappe.utils.now(),
		'status': 'Received'
	}).insert(ignore_permissions=True)


def run_before_send_method(doc=None, notification_type=None):
	from frappe.email.doctype.notification.notification import run_validate_notification

	if doc and notification_type:
		validation = run_validate_notification(
			doc, notification_type, throw=True
		)
		if not validation:
			frappe.throw(_("{0} Notification Validation Failed").format(notification_type))


def run_after_send_method(doctype=None, docname=None, notification_type=None):
	from frappe.core.doctype.notification_count.notification_count import add_notification_count

	if doctype and docname and notification_type:
		add_notification_count(doctype, docname, notification_type, 'WhatsApp')


def are_whatsapp_messages_muted():
	from frappe.utils import cint

	try:
		if getattr(frappe.flags, "mute_whatsapp", None):
			return True
	except RuntimeError:
		pass

	return cint(frappe.conf.get("mute_whatsapp") or 0) or False
