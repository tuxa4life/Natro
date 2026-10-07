package dev.natro

import android.Manifest
import android.app.role.RoleManager
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.core.app.NotificationManagerCompat
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

/** A setup step still to do, with the button that does it (or opens the right Settings page). */
data class SetupStep(val text: String, val button: String, val action: () -> Unit)

/**
 * Natro's screen: the conversation, a field to type, a button to hold while talking, and her questions.
 * Opened as the assistant (long-press), it starts listening right away.
 */
class MainActivity : ComponentActivity() {
    private val askMicrophone = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (!granted) Natro.note("Natro needs the microphone to hear you; typing works without it.")
        refreshSetup()
    }
    private val askPermissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        refreshSetup()
    }
    private val setup = mutableStateOf(emptyList<SetupStep>())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        startForegroundService(Intent(this, NatroService::class.java))
        if (ListenService.enabled(this) && granted(Manifest.permission.RECORD_AUDIO)) ListenService.setEnabled(this, true)
        setContent {
            val context = LocalContext.current
            val colors = if (isSystemInDarkTheme()) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
            MaterialTheme(colorScheme = colors) {
                NatroScreen(
                    setup = setup.value,
                    microphoneAllowed = { granted(Manifest.permission.RECORD_AUDIO) },
                    askMicrophone = { askMicrophone.launch(Manifest.permission.RECORD_AUDIO) },
                    setWakeWord = { on ->
                        if (on && !granted(Manifest.permission.RECORD_AUDIO)) askMicrophone.launch(Manifest.permission.RECORD_AUDIO)
                        else ListenService.setEnabled(this, on)
                    },
                )
            }
        }
        listenIfAsked(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        listenIfAsked(intent)
    }

    override fun onStart() {
        super.onStart()
        Natro.onScreen = true
    }

    override fun onResume() {
        super.onResume()
        refreshSetup()
    }

    override fun onStop() {
        Natro.onScreen = false
        super.onStop()
    }

    private fun listenIfAsked(intent: Intent?) {
        if (intent?.getBooleanExtra(LISTEN, false) != true) return
        intent.removeExtra(LISTEN)
        if (granted(Manifest.permission.RECORD_AUDIO)) Natro.listen() else askMicrophone.launch(Manifest.permission.RECORD_AUDIO)
    }

    private fun refreshSetup() {
        setup.value = buildList {
            if (!granted(Manifest.permission.POST_NOTIFICATIONS) || !granted(Manifest.permission.RECORD_AUDIO)) {
                add(SetupStep("Allow the microphone and Natro's notifications.", "Allow") {
                    askPermissions.launch(arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS))
                })
            }
            if (!getSystemService(RoleManager::class.java).isRoleHeld(RoleManager.ROLE_ASSISTANT)) {
                add(SetupStep("Make Natro your digital assistant: long-press the power button to talk to her.", "Open") {
                    startActivity(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS))
                })
            }
            if (PHONE_PERMISSIONS.any { !granted(it) }) {
                add(SetupStep("Let Natro use contacts, calls and texts (she always asks before calling or texting).",
                    "Allow") { askPermissions.launch(PHONE_PERMISSIONS) })
            }
            if (packageName !in NotificationManagerCompat.getEnabledListenerPackages(this@MainActivity)) {
                add(SetupStep("Let Natro read your notifications.", "Open") {
                    startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS).putExtra(
                        Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME,
                        ComponentName(this@MainActivity, NotificationReader::class.java).flattenToString()))
                })
            }
            if (ScreenControl.current == null) {
                add(SetupStep("Turn on screen control, so Natro can use apps for you (Accessibility > Natro).", "Open") {
                    startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                })
            }
        }
    }

    private fun granted(permission: String) = checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

    companion object {
        const val LISTEN = "dev.natro.LISTEN"
        val PHONE_PERMISSIONS = arrayOf(Manifest.permission.READ_CONTACTS, Manifest.permission.CALL_PHONE,
            Manifest.permission.SEND_SMS, Manifest.permission.READ_SMS)
    }
}

@Composable
fun NatroScreen(setup: List<SetupStep>, microphoneAllowed: () -> Boolean, askMicrophone: () -> Unit,
                setWakeWord: (Boolean) -> Unit) {
    val status by Natro.status.collectAsStateWithLifecycle()
    val listening by Natro.listening.collectAsStateWithLifecycle()
    val lines by Natro.lines.collectAsStateWithLifecycle()
    val question by Natro.question.collectAsStateWithLifecycle()
    val recording by Natro.recording.collectAsStateWithLifecycle()
    val speak by Natro.speakReplies.collectAsStateWithLifecycle()
    var typed by rememberSaveable { mutableStateOf("") }
    val list = rememberLazyListState()

    LaunchedEffect(lines.size, lines.lastOrNull()?.text) {
        if (lines.isNotEmpty()) list.animateScrollToItem(lines.lastIndex)
    }

    fun sendTyped() {
        if (typed.isNotBlank()) {
            Natro.sendText(typed.trim())
            typed = ""
        }
    }

    Scaffold { padding ->
        Column(Modifier.padding(padding).fillMaxSize().imePadding()) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                StatusLine(status, Modifier.weight(1f))
                Text("Speak", style = MaterialTheme.typography.labelMedium)
                Spacer(Modifier.width(8.dp))
                Switch(checked = speak, onCheckedChange = { Natro.speakReplies.value = it })
            }
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Listen for \"OK Natro\"", style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f))
                Switch(checked = listening, onCheckedChange = setWakeWord)
            }
            if (setup.isNotEmpty()) {
                Card(Modifier.fillMaxWidth().padding(16.dp)) {
                    Column(Modifier.padding(12.dp)) {
                        Text("To set up", style = MaterialTheme.typography.titleSmall)
                        setup.forEach { step ->
                            Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                                Text(step.text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                                TextButton(onClick = step.action) { Text(step.button) }
                            }
                        }
                    }
                }
            }

            LazyColumn(state = list, modifier = Modifier.weight(1f).fillMaxWidth(),
                contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(lines, key = { it.key }) { LineView(it) }
            }

            question?.let { asked ->
                Card(Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
                    Column(Modifier.padding(16.dp)) {
                        Text(asked.text, style = MaterialTheme.typography.bodyLarge)
                        Text(asked.details, style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Row(Modifier.fillMaxWidth().padding(top = 12.dp), horizontalArrangement = Arrangement.End) {
                            OutlinedButton(onClick = { Natro.answer(false) }) { Text("No") }
                            Spacer(Modifier.width(12.dp))
                            Button(onClick = { Natro.answer(true) }) { Text("Yes") }
                        }
                        Text("Or hold the mic and say yes or no.", style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }

            Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(value = typed, onValueChange = { typed = it }, modifier = Modifier.weight(1f),
                    placeholder = { Text("Type to Natro") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { sendTyped() }))
                TextButton(onClick = { sendTyped() }) { Text("Send") }
                Spacer(Modifier.width(8.dp))
                MicButton(recording, microphoneAllowed, askMicrophone)
            }
        }
    }
}

@Composable
private fun StatusLine(status: Natro.Status, modifier: Modifier) {
    val (text, color) = when (status) {
        Natro.Status.Online -> "Natro is online" to Color(0xFF2E7D32)
        Natro.Status.Connecting -> "Connecting…" to Color(0xFFF9A825)
        is Natro.Status.Offline -> "Offline: ${status.reason} (tap to retry)" to Color(0xFFC62828)
    }
    Row(modifier.clickable { Natro.reconnect() }, verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(10.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(8.dp))
        Text(text, style = MaterialTheme.typography.labelLarge, maxLines = 2)
    }
}

@Composable
private fun LineView(line: Natro.Line) {
    val mine = line.who == Natro.Who.YOU
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start) {
        when (line.who) {
            Natro.Who.TOOL, Natro.Who.NOTE -> Text(line.text, style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            else -> Text(line.text, style = MaterialTheme.typography.bodyLarge,
                color = if (mine) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.widthIn(max = 300.dp).clip(RoundedCornerShape(16.dp))
                    .background(if (mine) MaterialTheme.colorScheme.primaryContainer
                    else MaterialTheme.colorScheme.surfaceContainerHigh)
                    .padding(horizontal = 14.dp, vertical = 10.dp))
        }
    }
}

/** Hold to talk: recording starts on press and is sent on release. */
@Composable
private fun MicButton(recording: Boolean, microphoneAllowed: () -> Boolean, askMicrophone: () -> Unit) {
    val color = if (recording) Color(0xFFC62828) else MaterialTheme.colorScheme.primary
    Box(Modifier.size(64.dp).clip(CircleShape).background(color).pointerInput(Unit) {
        detectTapGestures(onPress = {
            if (Natro.recording.value) {  // listening on its own (as the assistant): a tap ends it
                Natro.stopRecording()
                return@detectTapGestures
            }
            if (!microphoneAllowed()) {
                askMicrophone()
                return@detectTapGestures
            }
            Natro.startRecording()
            tryAwaitRelease()
            Natro.stopRecording()
        })
    }, contentAlignment = Alignment.Center) {
        Icon(painterResource(R.drawable.ic_mic), contentDescription = "Hold to talk", tint = Color.White,
            modifier = Modifier.size(30.dp))
    }
}
