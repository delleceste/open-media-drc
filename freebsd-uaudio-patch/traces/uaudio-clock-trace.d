#pragma D option quiet
#pragma D option switchrate=10hz
#pragma D option bufsize=8m

BEGIN
{
	t0 = timestamp;
	printf("%-10s %-28s %s\n", "ms", "event", "detail");
}

/* ---- driver-level markers ---------------------------------------- */

fbt:snd_uaudio:uaudio_chan_start:entry
{
	printf("%-10d %-28s chan=%p\n",
	    (timestamp - t0) / 1000000, "uaudio_chan_start", (void *)arg0);
}

fbt:snd_uaudio:uaudio_chan_stop:entry
{
	printf("%-10d %-28s chan=%p\n",
	    (timestamp - t0) / 1000000, "uaudio_chan_stop", (void *)arg0);
}

fbt:snd_uaudio:uaudio_configure_msg_sub:entry
{
	printf("%-10d %-28s dir=%s\n",
	    (timestamp - t0) / 1000000, "configure_msg_sub ENTER",
	    (int)arg2 == 1 ? "PLAY" : "REC");
}

/* ---- the actual USB control transfers ---------------------------- */

/* UAC2 SET_CUR, CS = SAM_FREQ_CONTROL (0x01) : this is the clock write */
fbt::usbd_do_request_flags:entry
/((uint8_t *)arg2)[0] == 0x21 && ((uint8_t *)arg2)[1] == 0x01 &&
 ((uint8_t *)arg2)[3] == 0x01/
{
	self->tag = ">>> SET_CUR SAM_FREQ";
	printf("%-10d %-28s clockid=%d iface=%d rate=%d\n",
	    (timestamp - t0) / 1000000, self->tag,
	    ((uint8_t *)arg2)[5], ((uint8_t *)arg2)[4],
	    (uint32_t)((uint8_t *)arg3)[0] |
	    ((uint32_t)((uint8_t *)arg3)[1] << 8) |
	    ((uint32_t)((uint8_t *)arg3)[2] << 16) |
	    ((uint32_t)((uint8_t *)arg3)[3] << 24));
}

/* UAC2 GET_CUR, CS = SAM_FREQ_CONTROL : the read-back Linux does */
fbt::usbd_do_request_flags:entry
/((uint8_t *)arg2)[0] == 0xa1 && ((uint8_t *)arg2)[1] == 0x01 &&
 ((uint8_t *)arg2)[3] == 0x01/
{
	self->tag = "    GET_CUR SAM_FREQ";
	printf("%-10d %-28s clockid=%d iface=%d\n",
	    (timestamp - t0) / 1000000, self->tag,
	    ((uint8_t *)arg2)[5], ((uint8_t *)arg2)[4]);
}

/* Standard SET_INTERFACE : arming / parking a streaming interface */
fbt::usbd_do_request_flags:entry
/((uint8_t *)arg2)[0] == 0x01 && ((uint8_t *)arg2)[1] == 0x0b/
{
	self->tag = "  SET_INTERFACE";
	printf("%-10d %-28s iface=%d alt=%d\n",
	    (timestamp - t0) / 1000000, self->tag,
	    ((uint8_t *)arg2)[4], ((uint8_t *)arg2)[2]);
}

/* Did the device actually accept it?  usb_error_t 0 == USB_ERR_NORMAL_COMPLETION */
fbt::usbd_do_request_flags:return
/self->tag != NULL && arg1 != 0/
{
	printf("%-10d %-28s usb_error=%d  <<< REQUEST FAILED\n",
	    (timestamp - t0) / 1000000, "  !! error", (int)arg1);
}

fbt::usbd_do_request_flags:return
/self->tag != NULL/
{
	self->tag = 0;
}

END
{
	printf("\n-- trace ended --\n");
}
