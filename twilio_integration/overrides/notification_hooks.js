frappe.ui.form.on('Notification', {
	refresh: function(frm) {
		frm.events.toggle_template_fields(frm);
	},

	channel: function(frm) {
		frm.events.toggle_template_fields(frm);
	},

	use_whatsapp_template: function(frm) {
		frm.events.toggle_template_fields(frm);
	},

	toggle_template_fields: function(frm) {
		const is_whatsapp = frm.doc.channel === 'WhatsApp';
		const use_template = frm.doc.use_whatsapp_template;

		frm.set_df_property('use_whatsapp_template', 'hidden', !is_whatsapp);
		frm.set_df_property('whatsapp_message_template', 'hidden', !is_whatsapp || !use_template);
		frm.set_df_property('message_sb', 'hidden', is_whatsapp && use_template);
	}
});
