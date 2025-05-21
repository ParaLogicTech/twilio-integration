# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt
from rq.timeouts import JobTimeoutException
from requests.exceptions import ConnectionError, Timeout

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.password import get_decrypted_password
from frappe.utils import get_site_url, now_datetime
from ...twilio_handler import Twilio
import json


class WhatsAppMessage(Document):
	def on_trash(self):
		if frappe.session.user != 'Administrator':
			frappe.throw(_('Only Administrator can delete WhatsApp Message'))

	@classmethod
	def queue_whatsapp_message(
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
		now=False,
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
				content_variables=content_variables,
				notification_type=notification_type,
			)
			if not now:
				frappe.enqueue(
					"twilio_integration.twilio_integration.doctype.whatsapp_message.whatsapp_message.send_whatsapp_message",
					message_name=wa_msg.name, enqueue_after_commit=True)
			else:
				send_whatsapp_message(wa_msg.name, now=now)

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
		notification_type=None,
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
			'status': 'Not Sent',
			'retry': 0,
			'notification_type': notification_type,
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

	if not is_whatsapp_enabled():
		return True

	return frappe.flags.mute_whatsapp or cint(frappe.conf.get("mute_whatsapp") or 0) or False

def is_whatsapp_enabled():
	return True if frappe.get_cached_value("Twilio Settings", None, 'enabled') else False

def flush_whatsapp_message_queue(from_test=False):
	"""Flush queued WhatsApp Messages, called from scheduler"""
	auto_commit = not from_test

	if are_whatsapp_messages_muted():
		frappe.msgprint(_("WhatsApp messages are muted"))
		return

	for message in get_queued_whatsapp_messages():
		send_whatsapp_message(message.name, auto_commit)

def send_whatsapp_message(message_name, auto_commit=True, now=False):
	if are_whatsapp_messages_muted():
		frappe.msgprint(_("WhatsApp messages are muted"))
		return

	whatsapp_message = frappe.db.sql('''
		select *
		from `tabWhatsApp Message`
		where name = %s
		for update
		''', message_name, as_dict=True)[0]

	if whatsapp_message.status != "Not Sent":
		if auto_commit:
			frappe.db.rollback()
		return

	frappe.db.sql("""
		update `tabWhatsApp Message`
		set status='Sending',
		modified=%s
		where name=%s
		""", (now_datetime(), whatsapp_message.name), auto_commit=auto_commit)

	if whatsapp_message.communication:
		frappe.get_doc('Communication', whatsapp_message.communication).set_delivery_status(commit=auto_commit)

	try:
		client = Twilio.get_twilio_client()
		message_dict = get_whatsapp_message_dict(whatsapp_message)
		response = client.messages.create(**message_dict)

		frappe.db.sql("""UPDATE `tabWhatsApp Message`
			SET sent_received = %s,
			status = %s,
			id = %s,
			send_on = %s,
			modified = %s
			WHERE name = %s
			""",
			('Sent', response.status.title(), response.sid, response.date_sent, now_datetime(),whatsapp_message.name),
			auto_commit=auto_commit)

		if whatsapp_message.communication:
			frappe.get_doc('Communication', whatsapp_message.communication).set_delivery_status(commit=auto_commit)

		run_after_send_method(
			doctype=whatsapp_message.reference_doctype,
			docname=whatsapp_message.reference_document_name,
			notification_type=whatsapp_message.notification_type
		)


	except (ConnectionError, Timeout, JobTimeoutException):
		handle_timeout(whatsapp_message, auto_commit)

	except Exception as e:
		handle_error(e, whatsapp_message, auto_commit, now)

def get_whatsapp_message_dict(whatsapp_message):
	args = {
		'from_': whatsapp_message.from_,
		'to': whatsapp_message.to,
		'status_callback': '{}/api/method/twilio_integration.twilio_integration.api.whatsapp_message_status_callback'.format(
			get_site_url(frappe.local.site)
		)
	}

	if whatsapp_message.template_sid:
		args['content_sid'] = whatsapp_message.template_sid
		if whatsapp_message.content_variables:
			args['content_variables'] = whatsapp_message.content_variables
	else:
		args['body'] = whatsapp_message.message

	if whatsapp_message.media_link:
		args['media_url'] = [whatsapp_message.media_link]

	return args

def get_queued_whatsapp_messages():
	return frappe.db.sql('''
		select name
		from `tabWhatsApp Message`
		where status='Not Sent'
		order by priority desc, creation asc
		limit 500
	''', as_dict=True)

def clear_whatsapp_message_queue():
	"""Expire WhatsApp messages not sent for 7 days. Called daily via scheduler."""
	frappe.db.sql("""
		UPDATE `tabWhatsApp Message`
		SET status='Expired'
		WHERE modified < (NOW() - INTERVAL '7' DAY')
		AND status='Not Sent'""")

def handle_timeout(wa_message, auto_commit):
	frappe.db.sql("""
		update `tabWhatsApp Message`
		set status='Not Sent',
		modified=%s
		where name = %s""", (now_datetime(), wa_message.name), auto_commit=auto_commit)

	if wa_message.communication:
		frappe.get_doc('Communication', wa_message.communication).set_delivery_status(
			commit=auto_commit)

def handle_error(e, message, auto_commit, now):
	if auto_commit:
		frappe.db.rollback()

	if message.retry < 3:
		frappe.db.sql("""
			update `tabWhatsApp Message`
			set status='Not Sent', retry=retry+1, error=%s, modified=%s
			where name = %s
			""", (str(e), now_datetime(), message.name), auto_commit=auto_commit)
	else:
		frappe.db.sql("""
			update `tabWhatsApp Message`
			set status='Error', error=%s, modified=%s
			where name = %s
			""", (str(e), now_datetime(), message.name), auto_commit=auto_commit)

	if message.communication:
		frappe.get_doc('Communication', message.communication).set_delivery_status(commit=auto_commit)

	if now:
		print(frappe.get_traceback())
		raise e
	else:
		frappe.log_error(reference_doctype="WhatsApp Message", reference_name=message.name)
