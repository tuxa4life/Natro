package dev.natro

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Build
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Controlling apps by their screen (the owner turns it on in Settings > Accessibility > Natro).
 *
 * describe() turns the screen into numbered lines ("[4] button: Search (tap)"); the brain then
 * taps, types or scrolls by number, and each action returns the new screen. Buttons whose text
 * looks like sending, buying, paying or deleting are refused by tap() and need tapRisky(), which
 * the brain only runs after the owner's yes. Everything on screen is personal: only Claude sees it.
 */
class ScreenControl : AccessibilityService() {
    private var elements: List<AccessibilityNodeInfo> = emptyList()

    override fun onServiceConnected() {
        current = this
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        if (current === this) current = null
        return super.onUnbind(intent)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    override fun onInterrupt() {}

    fun describe(): String {
        val root = rootInActiveWindow ?: throw ToolFailure("Can't read the screen right now (it may be locked or off).")
        val found = mutableListOf<AccessibilityNodeInfo>()
        val lines = mutableListOf<String>()
        fun visit(node: AccessibilityNodeInfo) {
            if (found.size >= MAX_ELEMENTS || !node.isVisibleToUser) return
            val label = label(node)
            val actions = buildList {
                if (node.isClickable) add("tap")
                if (node.isEditable) add("edit")
                if (node.isScrollable) add("scroll")
                if (node.isCheckable) add(if (checked(node)) "on" else "off")
                if (!node.isEnabled) add("disabled")
            }
            if (label.isNotBlank() || node.isEditable || node.isScrollable || (node.isClickable && childText(node).isNotBlank())) {
                found += node
                val shown = label.ifBlank { childText(node) }
                lines += "[${found.size}] ${kind(node)}: ${Text.short(shown)}" + if (actions.isEmpty()) "" else " (${actions.joinToString(", ")})"
            }
            for (i in 0 until node.childCount) node.getChild(i)?.let(::visit)
        }
        visit(root)
        elements = found
        val app = root.packageName?.toString().orEmpty()
        val cut = if (found.size >= MAX_ELEMENTS) "\n(more on screen than shown)" else ""
        return "Screen of $app:\n" + lines.joinToString("\n") + cut
    }

    fun tap(number: Int, risky: Boolean): String {
        val node = element(number)
        val target = clickableSelfOrParent(node) ?: node
        val words = listOf(label(node), label(target), childText(target)).joinToString(" ")
        if (!risky && Text.RISKY.containsMatchIn(words)) {
            throw ToolFailure("[$number] (${Text.short(words, 40)}) looks like it sends, buys, pays or deletes: use " +
                "tap_risky, which asks him first.")
        }
        if (!target.performAction(AccessibilityNodeInfo.ACTION_CLICK)) tapAt(target)
        return after("Tapped [$number].")
    }

    fun type(number: Int, text: String): String {
        val node = element(number)
        if (!node.isEditable) throw ToolFailure("[$number] isn't a text field.")
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        val args = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text) }
        if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) throw ToolFailure("Couldn't type into [$number].")
        return after("Typed into [$number].")
    }

    fun scroll(down: Boolean, number: Int?): String {
        val node = if (number != null) element(number) else elements.firstOrNull { it.refresh() && it.isScrollable }
            ?: throw ToolFailure("Nothing on this screen scrolls (read the screen again first).")
        val action = if (down) AccessibilityNodeInfo.ACTION_SCROLL_FORWARD else AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
        if (!node.performAction(action)) throw ToolFailure("It won't scroll further that way.")
        return after("Scrolled ${if (down) "down" else "up"}.")
    }

    fun press(key: String): String {
        val action = mapOf(
            "back" to GLOBAL_ACTION_BACK, "home" to GLOBAL_ACTION_HOME, "recents" to GLOBAL_ACTION_RECENTS,
            "notifications" to GLOBAL_ACTION_NOTIFICATIONS, "quick_settings" to GLOBAL_ACTION_QUICK_SETTINGS,
        )[key] ?: throw ToolFailure("The key is back, home, recents, notifications or quick_settings.")
        if (!performGlobalAction(action)) throw ToolFailure("Android didn't take that.")
        return after("Pressed $key.")
    }

    private fun element(number: Int): AccessibilityNodeInfo {
        val node = elements.getOrNull(number - 1) ?: throw ToolFailure("There's no [$number]: read the screen first.")
        if (!node.refresh()) throw ToolFailure("The screen changed since it was read: read it again.")
        return node
    }

    /** The new screen, once the app has had a moment to react. */
    private fun after(done: String): String {
        Thread.sleep(SETTLE_MILLIS)
        return "$done Now:\n${describe()}"
    }

    private fun tapAt(node: AccessibilityNodeInfo) {
        val bounds = Rect().also(node::getBoundsInScreen)
        val path = Path().apply { moveTo(bounds.exactCenterX(), bounds.exactCenterY()) }
        dispatchGesture(GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(path, 0, 60)).build(),
            null, null)
    }

    companion object {
        private const val MAX_ELEMENTS = 150
        private const val SETTLE_MILLIS = 900L

        private fun label(node: AccessibilityNodeInfo): String =
            listOfNotNull(node.text, node.contentDescription, node.hintText).joinToString(" · ") { it.toString() }.trim()

        private fun childText(node: AccessibilityNodeInfo): String {
            val texts = mutableListOf<String>()
            fun visit(child: AccessibilityNodeInfo, depth: Int) {
                if (depth > 3 || texts.size >= 4) return
                label(child).takeIf { it.isNotBlank() }?.let(texts::add)
                for (i in 0 until child.childCount) child.getChild(i)?.let { visit(it, depth + 1) }
            }
            for (i in 0 until node.childCount) node.getChild(i)?.let { visit(it, 0) }
            return texts.joinToString(" · ")
        }

        private fun clickableSelfOrParent(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
            var at: AccessibilityNodeInfo? = node
            repeat(6) {
                if (at?.isClickable == true) return at
                at = at?.parent
            }
            return null
        }

        private fun checked(node: AccessibilityNodeInfo): Boolean =
            if (Build.VERSION.SDK_INT >= 36) node.checked == AccessibilityNodeInfo.CHECKED_STATE_TRUE
            else @Suppress("DEPRECATION") node.isChecked

        private fun kind(node: AccessibilityNodeInfo): String = when {
            node.isEditable -> "field"
            node.isCheckable -> "switch"
            node.className?.contains("Button") == true -> "button"
            node.className?.contains("Image") == true -> "image"
            node.isScrollable -> "list"
            node.isClickable -> "item"
            else -> "text"
        }

        @Volatile var current: ScreenControl? = null
    }
}
