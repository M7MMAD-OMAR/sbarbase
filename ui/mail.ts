/** Read only display of the two operator surfaces the control plane now answers.
 *
 * The values come from the API as they are recorded there: the mail entry is the runtime's
 * own non secret summary (`lab/mail_state.py`), and the notification state is the catalog's
 * own read. Nothing here derives a field the API does not return, and nothing here renders
 * a credential, because the API carries none.
 */
export type MailEntry = {
  state: string;
  credentials?: 'set' | 'none';
  at?: number;
  host?: string;
  port?: number;
  admin_email?: string;
  sender_name?: string;
  reply_to?: string;
  max_frequency?: string;
  otp_exp?: number;
  otp_length?: number;
  secure_email_change?: boolean;
  autoconfirm?: boolean;
  rate_limit_email_sent?: string;
  rate_limit_otp?: number;
  rate_limit_verify?: number;
  rate_limit_header?: string;
};

const LABEL: Record<string, string> = {
  unconfigured: 'Not configured',
  applied: 'On',
  failed: 'Failed',
  off: 'Off',
};

const TEXT: Record<string, string> = {
  unconfigured: 'Email is off for this environment. Confirmation, recovery and magic link messages are not sent.',
  applied: 'Mail is on for this environment only. The environment Auth service was restarted to apply it.',
  failed: 'Email could not be applied. The environment keeps working and sends no mail.',
  off: 'A previous configuration was removed. Email is off for this environment.',
};

/** The four states the runtime names, and the unconfigured state for an absent entry. */
export function mailState(entry?: MailEntry) {
  const state = entry?.state && TEXT[entry.state] ? entry.state : 'unconfigured';
  return { state, label: LABEL[state]!, text: TEXT[state]! };
}

type Row = { label: string; value: string };

/** The facts the state was applied with. `empty` is the summary's own marker for an unset
 * optional field, so it is not rendered as a value. */
export function mailDetails(entry?: MailEntry): Row[] {
  const rows: Row[] = [];
  if (!entry) return rows;
  if (typeof entry.host === 'string') rows.push({ label: 'Relay', value: entry.port ? entry.host + ':' + entry.port : entry.host });
  if (typeof entry.admin_email === 'string') {
    const sender = entry.sender_name && entry.sender_name !== 'empty' ? entry.sender_name + ' <' + entry.admin_email + '>' : entry.admin_email;
    rows.push({ label: 'Sender', value: sender });
  }
  if (entry.reply_to && entry.reply_to !== 'empty') rows.push({ label: 'Reply to', value: entry.reply_to });
  if (typeof entry.rate_limit_email_sent === 'string') rows.push({ label: 'Email rate limit', value: entry.rate_limit_email_sent });
  if (typeof entry.otp_exp === 'number' && typeof entry.otp_length === 'number') {
    rows.push({ label: 'One time code', value: entry.otp_length + ' digits, valid ' + entry.otp_exp + ' seconds' });
  }
  return rows;
}

export function notificationText(count: number): string {
  return 'Undelivered notifications: ' + count;
}