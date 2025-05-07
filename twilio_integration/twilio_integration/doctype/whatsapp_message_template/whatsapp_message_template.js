frappe.ui.form.on('WhatsApp Message Template', {
	refresh: function (frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__('Sync Template'), () => {
				frappe.call({
					method: 'twilio_integration.twilio_integration.doctype.whatsapp_message_template.whatsapp_message_template.sync_twilio_template',
					args: {
						template_sid: frm.doc.template_sid,
						template_name: frm.doc.name,
					},
					callback: function (r) {
						if (r.message) {
							frappe.msgprint(r.message);
							frm.reload_doc();
						}
					}
				});
			});

			frm.page.set_inner_btn_icon(__('Sync Template'), 'refresh');
		}
	}
});
