# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.password import get_decrypted_password
from frappe.utils import get_site_url, convert_utc_to_system_timezone, time_diff, now_datetime, cint, flt
from ...twilio_handler import Twilio
from urllib.parse import quote, urlparse
from datetime import timedelta
import json


class WhatsAppMessage(Document):
	def on_trash(self):
		if frappe.session.user != 'Administrator':
			frappe.throw(_('Only Administrator can delete WhatsApp Message'))

	def get_message_dict(self):
		site_url = get_site_url(frappe.local.site)

		args = {
			"from_": self.from_,
			"to": self.to,
			"status_callback": f"{site_url}/api/method/twilio.whatsapp_message_status_callback"
		}

		if self.template_sid:
			args['content_sid'] = self.template_sid
			if self.content_variables:
				args['content_variables'] = self.content_variables
		else:
			args['body'] = self.message

		attachment = self.get_attachment()
		if attachment:
			args['media_url'] = [f"{site_url}/api/method/twilio.whatsapp_media?id={quote(self.name)}"]

		return args

	def get_attachment(self, store_print_attachment=False):
		attachment = None
		if self.attachment:
			attachment = json.loads(self.attachment)

		if not attachment:
			return None

		if store_print_attachment and attachment.get("print_format_attachment") == 1:
			updated_attachment = self.store_print_attachment(attachment, auto_commit=True)
			if updated_attachment:
				attachment = updated_attachment

		return attachment

	def store_print_attachment(self, attachment, auto_commit=False):
		if not frappe.get_system_settings("store_attached_pdf_document"):
			return

		print_format_file = self.get_print_format_file(attachment)

		file_data = frappe._dict(file_name=print_format_file["fname"], is_private=1)

		# Store on communication if available, else email queue doc
		if self.communication:
			file_data.attached_to_doctype = "Communication"
			file_data.attached_to_name = self.communication
		else:
			file_data.attached_to_doctype = self.doctype
			file_data.attached_to_name = self.name

		fid = frappe.db.get_value("File", file_data)
		if not fid:
			file = frappe.new_doc("File", **file_data)
			file.content = print_format_file["fcontent"]
			file.insert(ignore_permissions=True)
			fid = file.name

		# not needed becuase twilio downloads the file before sending message and callback
		# if self.communication:
		# 	frappe.get_doc("Communication", self.communication).notify_change("update")

		updated_attachment = {"fid": fid}
		self.db_set("attachment", json.dumps(updated_attachment), commit=auto_commit)

		return updated_attachment

	@classmethod
	def get_print_format_file(cls, attachment):
		attachment = attachment.copy()
		attachment.pop("print_format_attachment", None)
		print_format_file = frappe.attach_print(**attachment)
		return print_format_file

	@classmethod
	def send_whatsapp_message(
		cls,
		receiver_list,
		message=None,
		notification_type=None,
		reference_doctype=None,
		reference_name=None,
		child_doctype=None,
		child_name=None,
		party_doctype=None,
		party=None,
		whatsapp_message_template=None,
		whatsapp_reply_handler=None,
		content_variables=None,
		attachment=None,
		automated=False,
		delayed=False,
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

		communication = cls.create_outgoing_communication(
			receiver_list=receiver_list,
			message=message,
			reference_doctype=reference_doctype,
			reference_name=reference_name,
			party_doctype=party_doctype,
			party=party,
			attachment=attachment,
			automated=automated,
		)

		doc = get_doc_for_notification_triggers(reference_doctype, reference_name)
		run_before_send_method(doc=doc, notification_type=notification_type)

		for rec in receiver_list:
			wa_msg = cls.store_whatsapp_message(
				to=rec,
				message=message,
				reference_doctype=reference_doctype,
				reference_docname=reference_name,
				child_doctype=child_doctype,
				child_name=child_name,
				party_doctype=party_doctype,
				party=party,
				communication=communication,
				attachment=attachment,
				whatsapp_message_template=whatsapp_message_template,
				whatsapp_reply_handler=whatsapp_reply_handler,
				content_variables=content_variables,
				notification_type=notification_type,
			)

			if not delayed:
				if now:
					send_whatsapp_message(wa_msg.name, auto_commit=not now, now=now)
				else:
					frappe.enqueue(
						"twilio_integration.twilio_integration.doctype.whatsapp_message.whatsapp_message.send_whatsapp_message",
						message_name=wa_msg.name,
						enqueue_after_commit=True
					)

	@classmethod
	def create_outgoing_communication(
		cls,
		receiver_list,
		message,
		reference_doctype,
		reference_name,
		party_doctype=None,
		party=None,
		attachment=None,
		automated=False,
	):
		from frappe.core.doctype.communication.email import add_attachments

		if not reference_doctype or not reference_name:
			return

		communication = frappe.get_doc({
			"doctype": "Communication",
			"communication_type": "Automated Message" if automated else "Communication",
			"communication_medium": "WhatsApp",
			"subject": "WhatsApp Message Sent",
			"content": message,
			"sent_or_received": "Sent",
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"sender": frappe.session.user,
			"recipients": "\n".join(receiver_list),
			"phone_no": receiver_list[0] if len(receiver_list) == 1 else None,
			"has_attachment": 1 if attachment else 0,
		})

		if party_doctype and party:
			communication.append("timeline_links", {
				"link_doctype": party_doctype,
				"link_name": party
			})

		communication.insert(ignore_permissions=True)

		if attachment:
			if isinstance(attachment, str):
				attachment = json.loads(attachment)
			add_attachments(communication.name, [attachment])

		return communication.get("name")

	@classmethod
	def create_incoming_communication(
		cls,
		from_,
		to,
		message,
		reference_doctype,
		reference_name,
		party_doctype=None,
		party=None,
		profile_name=None,
		in_reply_to=None,
		attachment=None,
	):
		if not reference_doctype or not reference_name:
			return

		to_number = to
		if to_number.startswith("whatsapp:"):
			to_number = to_number[9:]

		from_number = from_
		if from_number.startswith("whatsapp:"):
			from_number = from_number[9:]

		sender_name = profile_name
		if sender_name:
			sender_name = f"{sender_name} ({from_number})"
		else:
			sender_name = from_number

		communication = frappe.get_doc({
			"doctype": "Communication",
			"communication_type": "Communication",
			"communication_medium": "WhatsApp",
			"subject": "WhatsApp Message Received",
			"content": message,
			"sent_or_received": "Received",
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"recipients": to_number,
			# "sender": from_number,
			"sender_full_name": sender_name,
			"phone_no": from_number,
			"in_reply_to": in_reply_to,
			"has_attachment": 1 if attachment else 0,
		})

		if party_doctype and party:
			communication.append("timeline_links", {
				"link_doctype": party_doctype,
				"link_name": party
			})

		communication.insert(ignore_permissions=True)
		return communication.get("name")

	@classmethod
	def store_whatsapp_message(
		cls,
		to,
		message=None,
		reference_doctype=None,
		reference_docname=None,
		child_doctype=None,
		child_name=None,
		party_doctype=None,
		party=None,
		communication=None,
		attachment=None,
		whatsapp_message_template=None,
		whatsapp_reply_handler=None,
		content_variables=None,
		notification_type=None,
	):
		sender = frappe.db.get_single_value('Twilio Settings', 'whatsapp_no')

		template = frappe.get_cached_doc("WhatsApp Message Template", whatsapp_message_template) if whatsapp_message_template else frappe._dict()
		reply_handler = template.reply_handler if template else whatsapp_reply_handler

		wa_msg = frappe.new_doc("WhatsApp Message")
		wa_msg.update({
			'sent_received': 'Sent',
			'from_': f'whatsapp:{sender}',
			'to': f'whatsapp:{to}',
			'message': message,
			'reference_doctype': reference_doctype,
			'reference_name': reference_docname,
			'child_doctype': child_doctype,
			'child_name': child_name,
			'party_doctype': party_doctype,
			'party': party,
			'attachment': json.dumps(attachment) if attachment else None,
			'communication': communication,
			'notification_type': notification_type,
			'template_sid': template.template_sid or None,
			'reply_handler': reply_handler or None,
			'status': 'Not Sent',
			'retry': 0,
		})
		wa_msg.insert(ignore_permissions=True)

		if template.media_variable and template.media_variable not in content_variables:
			if template.media_variable_type == "Message ID":
				content_variables[template.media_variable] = quote(wa_msg.name)
			else:
				content_variables[template.media_variable] = f"api/method/twilio.whatsapp_media?id={quote(wa_msg.name)}"

		if content_variables:
			wa_msg.db_set("content_variables", json.dumps(content_variables))

		return wa_msg

	@classmethod
	def get_last_indirect_reply_message(cls, to, from_):
		message = frappe.db.sql("""
			select m.name, m.date_sent, m.reply_handler, h.expiry_indirect_reply, m.reply_handler_expired
			from `tabWhatsApp Message` m
			inner join `tabWhatsApp Reply Handler` h on h.name = m.reply_handler
			where m.`to` = %(to)s
				and m.from_ = %(from)s
				and m.sent_received = 'Sent'
				and m.status in ('Delivered', 'Read')
				and m.date_sent is not null
				and h.allow_indirect_reply = 1
			order by date_sent desc
			limit 1
		""", {
			"to": to,
			"from": from_,
		}, as_dict=True)

		message = message[0] if message else None
		if not message:
			return None

		if message.reply_handler_expired:
			return None

		window_seconds = cint(message.expiry_indirect_reply)
		if window_seconds > 0:
			now = now_datetime()
			window_timedelta = timedelta(seconds=window_seconds)
			diff = time_diff(now, message.date_sent)
			if diff > window_timedelta:
				return None

		return message.name

	@classmethod
	def get_replied_to_message(cls, original_sid, sender):
		return frappe.db.get_value("WhatsApp Message", {
			"id": original_sid,
			"from_": sender,
			"sent_received": "Sent",
		})

	def update_message_delivery_status(cls):
		"""
		This method Reconciles delivery status for a single message with status 'Sent' or 'Queued'
		"""
		if are_whatsapp_messages_muted():
			frappe.msgprint(_("WhatsApp messages are muted"))
			return

		if cls.status not in ('Sent', 'Queued'):
			return

		original_message_status = cls.status
		new_message_status = Twilio.get_message(cls.id).status.title()

		if not new_message_status or (new_message_status == original_message_status):
			return

		cls.db_set("status", new_message_status, commit=False)

		if cls.communication:
			frappe.get_doc('Communication', cls.communication).set_delivery_status(commit=False)

def outgoing_message_status_callback(args, auto_commit=False):
	message = frappe.db.get_value("WhatsApp Message", filters={
		'id': args.MessageSid,
		'from_': args.From,
		'to': args.To
	}, fieldname=["name", "communication"], as_dict=1)

	if message:
		frappe.db.set_value("WhatsApp Message", message.name, {
			"status": args.MessageStatus.title(),
		})
		if auto_commit:
			frappe.db.commit()

		if message.communication:
			comm = frappe.get_doc("Communication", message.communication)
			comm.set_delivery_status(commit=auto_commit)


def run_before_send_method(doc=None, notification_type=None):
	from frappe.email.doctype.notification.notification import run_validate_notification

	if doc and notification_type:
		validation = run_validate_notification(
			doc, notification_type, throw=True
		)
		if not validation:
			frappe.throw(_("{0} Notification Validation Failed").format(notification_type))


def run_after_send_method(reference_doctype=None, reference_name=None, notification_type=None):
	from frappe.core.doctype.notification_count.notification_count import add_notification_count

	if reference_doctype and reference_name and notification_type:
		add_notification_count(reference_doctype, reference_name, notification_type, 'WhatsApp')


def are_whatsapp_messages_muted():
	if not is_whatsapp_enabled():
		return True

	return frappe.flags.mute_whatsapp or cint(frappe.conf.get("mute_whatsapp") or 0) or False


def is_whatsapp_enabled():
	return True if frappe.get_cached_value("Twilio Settings", None, 'enabled') else False


def flush_outgoing_message_queue(from_test=False):
	"""Flush queued WhatsApp Messages, called from scheduler"""
	auto_commit = not from_test

	if are_whatsapp_messages_muted():
		frappe.msgprint(_("WhatsApp messages are muted"))
		return

	for message_name in get_queued_outgoing_messages():
		send_whatsapp_message(message_name, auto_commit=auto_commit)


def send_whatsapp_message(message_name, auto_commit=True, now=False):
	from frappe.email.doctype.notification.notification import get_doc_for_notification_triggers

	if are_whatsapp_messages_muted():
		frappe.msgprint(_("WhatsApp messages are muted"))
		return

	message_doc = frappe.get_doc("WhatsApp Message", message_name, for_update=True)

	if message_doc.status != "Not Sent" or message_doc.sent_received != "Sent":
		if auto_commit:
			frappe.db.rollback()
		return

	message_doc.db_set("status", "Sending", commit=auto_commit)
	if message_doc.communication:
		frappe.get_doc('Communication', message_doc.communication).set_delivery_status(commit=auto_commit)

	try:
		doc = get_doc_for_notification_triggers(message_doc.reference_doctype, message_doc.reference_name)
		run_before_send_method(doc, notification_type=message_doc.notification_type)

		client = Twilio.get_twilio_client()
		message_dict = message_doc.get_message_dict()
		response = client.messages.create(**message_dict)

		date_sent = response.date_sent or response.date_created
		if date_sent:
			date_sent = convert_utc_to_system_timezone(date_sent).replace(tzinfo=None)

		message_doc.db_set({
			"id": response.sid,
			"status": response.status.title(),
			"date_sent": date_sent,
			"error": None,
		}, commit=auto_commit)

		if message_doc.communication:
			frappe.get_doc('Communication', message_doc.communication).set_delivery_status(commit=auto_commit)

		run_after_send_method(
			reference_doctype=message_doc.reference_doctype,
			reference_name=message_doc.reference_name,
			notification_type=message_doc.notification_type
		)

	except Exception as e:
		if auto_commit:
			frappe.db.rollback()

		if message_doc.retry < 3:
			message_doc.db_set({
				"status": "Not Sent",
				"retry": message_doc.retry + 1,
				"error": str(e),
			}, commit=auto_commit)
		else:
			message_doc.db_set({
				"status": "Error",
				"error": str(e),
			}, commit=auto_commit)

		if message_doc.communication:
			frappe.get_doc('Communication', message_doc.communication).set_delivery_status(commit=auto_commit)

		if now:
			raise e
		else:
			frappe.log_error(
				title=_("Failed to send WhatsApp Message"),
				message=str(e),
				reference_doctype="WhatsApp Message",
				reference_name=message_doc.name
			)


def flush_incoming_media_queue(from_test=False):
	"""Flush queued WhatsApp Messages, called from scheduler"""
	auto_commit = not from_test

	if are_whatsapp_messages_muted():
		frappe.msgprint(_("WhatsApp messages are muted"))
		return

	for message_name in get_queued_incoming_media_messages():
		download_incoming_media(message_name, auto_commit=auto_commit)


def download_incoming_media(message_name, auto_commit=True, now=False):
	import mimetypes
	import os

	if are_whatsapp_messages_muted():
		frappe.msgprint(_("WhatsApp messages are muted"))
		return

	if isinstance(message_name, Document):
		message_doc = message_name
	else:
		message_doc = frappe.get_doc("WhatsApp Message", message_name, for_update=True)

	if message_doc.incoming_media_status != "To Download" or message_doc.sent_received != "Received":
		if auto_commit:
			frappe.db.rollback()
		return

	attachment = message_doc.get_attachment()
	if not attachment or not attachment.get("media_url") or attachment.get("fid"):
		message_doc.db_set({
			"incoming_media_status": "Attached" if attachment and attachment.get("fid") else None
		}, commit=auto_commit)
		return

	message_doc.db_set("incoming_media_status", "Downloading", commit=auto_commit)

	try:
		media_url = attachment.get("media_url")
		mime_type = attachment.get("mime_type")

		file_extension = mimetypes.guess_extension(mime_type)
		media_sid = os.path.basename(urlparse(media_url).path)
		filename = '{sid}{ext}'.format(sid=media_sid, ext=file_extension)

		response = Twilio.download_media_request(media_url)

		file_data = frappe._dict(file_name=filename, is_private=1)
		if message_doc.communication:
			file_data.attached_to_doctype = "Communication"
			file_data.attached_to_name = message_doc.communication
		else:
			file_data.attached_to_doctype = message_doc.doctype
			file_data.attached_to_name = message_doc.name

		file = frappe.new_doc("File", **file_data)
		file.content = response.content
		file.insert(ignore_permissions=True)

		fid = file.name

		if message_doc.communication:
			frappe.get_doc("Communication", message_doc.communication).notify_change("update")

		updated_attachment = attachment.copy()
		updated_attachment["fid"] = fid
		message_doc.db_set({
			"incoming_media_status": "Attached",
			"attachment": json.dumps(updated_attachment),
			"error": None,
		}, commit=auto_commit)

	except Exception as e:
		if auto_commit:
			frappe.db.rollback()

		if message_doc.retry < 3:
			message_doc.db_set({
				"incoming_media_status": "To Download",
				"retry": message_doc.retry + 1,
				"error": str(e),
			}, commit=auto_commit)
		else:
			message_doc.db_set({
				"incoming_media_status": "Error",
				"error": str(e),
			}, commit=auto_commit)

		if now:
			raise e
		else:
			frappe.log_error(
				title=_("Failed to download incoming WhatsApp media"),
				message=str(e),
				reference_doctype="WhatsApp Message",
				reference_name=message_doc.name
			)


def get_queued_outgoing_messages():
	return frappe.db.sql_list("""
		select name
		from `tabWhatsApp Message`
		where status = 'Not Sent' and sent_received = 'Sent'
		order by priority desc, creation asc
		limit 500
	""")


def get_queued_incoming_media_messages():
	return frappe.db.sql_list("""
		select name
		from `tabWhatsApp Message`
		where incoming_media_status = 'To Download' and sent_received = 'Received'
		order by priority desc, creation asc
		limit 100
	""")


def expire_whatsapp_message_queue():
	"""Expire WhatsApp messages not sent for 7 days. Called daily via scheduler."""
	frappe.db.sql("""
		UPDATE `tabWhatsApp Message`
		SET status = 'Expired'
		WHERE modified < (NOW() - INTERVAL '7' DAY) AND status = 'Not Sent'
	""")


def incoming_message_callback(args):
	out = frappe._dict({
		"reply_message": None,
		"disable_default_reply": False,
	})

	# Determine previous outgoing message for context
	if args.OriginalRepliedMessageSid:
		context_message_name = WhatsAppMessage.get_replied_to_message(
			args.OriginalRepliedMessageSid,
			args.OriginalRepliedMessageSender
		)
	else:
		context_message_name = WhatsAppMessage.get_last_indirect_reply_message(args.From, args.To)

	# Do not receive message if there is no context
	if not context_message_name:
		return out

	context_message = frappe.get_doc("WhatsApp Message", context_message_name)

	if context_message.reply_handler:
		reply_handler = frappe.get_cached_doc("WhatsApp Reply Handler", context_message.reply_handler)
	else:
		reply_handler = frappe._dict()

	incoming_message = frappe.new_doc("WhatsApp Message")
	incoming_message.update({
		"from_": args.From,
		"to": args.To,
		"message": args.Body,
		"profile_name": args.ProfileName,
		"sent_received": "Received",
		"id": args.MessageSid,
		"date_sent": frappe.utils.now(),
		"status": "Received",

		"reply_handler": reply_handler.name,
		"context_message": context_message.name,
		"party_doctype": context_message.party_doctype,
		"party": context_message.party,
	})

	if (
		context_message.reference_doctype
		and context_message.reference_name
		and frappe.db.exists(context_message.reference_doctype, context_message.reference_name)
	):
		incoming_message.update({
			"reference_doctype": context_message.reference_doctype,
			"reference_name": context_message.reference_name,
		})

	# Store media details
	attachment = None
	incoming_message.incoming_media_status = None
	if args.MediaUrl0:
		attachment = {"media_url": args.MediaUrl0, "mime_type": args.MediaContentType0}
		incoming_message.attachment = json.dumps(attachment)
		incoming_message.incoming_media_status = "To Download"

	# Create Communication
	incoming_message.communication = WhatsAppMessage.create_incoming_communication(
		from_=args.From,
		to=args.To,
		message=args.Body,
		reference_doctype=incoming_message.reference_doctype,
		reference_name=incoming_message.reference_name,
		party_doctype=incoming_message.party_doctype,
		party=incoming_message.party,
		profile_name=args.ProfileName,
		in_reply_to=context_message.communication,
		attachment=attachment,
	)

	incoming_message.insert(ignore_permissions=True)

	frappe.db.commit()

	# Download attachment
	if reply_handler and reply_handler.download_media_before_handling:
		download_incoming_media(incoming_message)
	elif incoming_message.incoming_media_status == "To Download":
		frappe.enqueue(
			"twilio_integration.twilio_integration.doctype.whatsapp_message.whatsapp_message.download_incoming_media",
			message_name=incoming_message.name,
			enqueue_after_commit=False,
			queue="long",
		)

	# Handle reply
	if reply_handler and not context_message.reply_handler_expired:
		out.disable_default_reply = True

		original_user = frappe.session.user

		try:
			frappe.set_user("Administrator")
			reply_message = reply_handler.handle_incoming_message(incoming_message, context_message)
			if reply_message:
				out.reply_message = reply_message

			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			reply_handler.log_error(title="Error handling WhatsApp Message Reply", message=frappe.get_traceback())
			out.reply_message = reply_handler.error_reply_message
		finally:
			frappe.set_user(original_user)

	return out


def on_doctype_update():
	frappe.db.add_index('WhatsApp Message', ('status', 'priority', 'creation'), 'index_bulk_flush')
	frappe.db.add_index('WhatsApp Message', ('incoming_media_status', 'priority', 'creation'), 'index_incoming_media')
	frappe.db.add_index('WhatsApp Message', ('`to`', 'status', 'date_sent'), 'index_indirect_reply')


def reconcile_all_message_delivery_status(limit=100, auto_commit=True):
	"""
	Reconcile delivery status for all messages with status 'Sent' or 'Queued'
	This method processes messages in batches with proper error handling
	"""
	if are_whatsapp_messages_muted():
		frappe.msgprint(_("WhatsApp messages are muted"))
		return

	messages_to_reconcile = get_pending_reconciliation_messages(limit)

	for msg_data in messages_to_reconcile:
		try:
			message_doc = frappe.get_doc("WhatsApp Message", msg_data.name, for_update=True)
			message_doc.update_message_delivery_status()

			if auto_commit:
				frappe.db.commit()

		except Exception as e:
			if auto_commit:
				frappe.db.rollback()

			frappe.log_error(
				title=_("Error in WhatsApp Message Delivery Status Reconciliation"),
				message=str(e),
			)


def get_pending_reconciliation_messages(limit):
	"""
	Fetch WhatsApp messages with status 'Sent' or 'Queued' and that haven't received delivery confirmation
	"""
	return frappe.db.sql("""
		SELECT name, id
		FROM `tabWhatsApp Message`
		WHERE status IN ('Sent', 'Queued')
		AND sent_received = 'Sent'
		AND id IS NOT NULL
		ORDER BY creation DESC
		LIMIT %s
		""", (limit,), as_dict=True)
