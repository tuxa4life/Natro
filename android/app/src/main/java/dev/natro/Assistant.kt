package dev.natro

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService
import android.speech.RecognitionService
import android.speech.SpeechRecognizer

/**
 * Natro as the phone's digital assistant: long-pressing the power button (or the assistant
 * gesture) opens her, already listening. Being the assistant also lets her open apps, alarms
 * and calls while she isn't on screen: Android allows that to the app whose
 * VoiceInteractionService it keeps bound.
 *
 * The owner picks her in Settings > Apps > Default apps > Digital assistant app.
 */
class NatroVoiceService : VoiceInteractionService()

class NatroSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = NatroSession(this)
}

/** Opens Natro's screen in listening mode, then gets out of the way. */
class NatroSession(context: Context) : VoiceInteractionSession(context) {
    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        startAssistantActivity(Intent(context, MainActivity::class.java).putExtra(MainActivity.LISTEN, true))
        hide()
    }
}

/** An assistant must name a speech recognizer; Natro's speech goes to her brain instead, so this one declines. */
class NatroRecognitionService : RecognitionService() {
    override fun onStartListening(recognizerIntent: Intent?, listener: Callback?) {
        listener?.error(SpeechRecognizer.ERROR_CLIENT)
    }

    override fun onCancel(listener: Callback?) {}

    override fun onStopListening(listener: Callback?) {}
}
