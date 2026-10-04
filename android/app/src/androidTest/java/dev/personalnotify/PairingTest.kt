package dev.personalnotify

import org.junit.Assert.*
import org.junit.Test

class PairingTest {
    @Test fun validTicketAndUnsafeTargets() {
        val code = "a".repeat(43)
        val ticket = Pairing.parse("personalnotify://pair?server=https%3A%2F%2Fnotify.example&code=$code")
        assertEquals("https://notify.example", ticket.server)
        assertEquals(code, ticket.code)
        for (text in listOf(
            "personalnotify://pair?server=http%3A%2F%2Fnotify.example&code=$code",
            "personalnotify://pair?server=https%3A%2F%2Fu%3Ap%40notify.example&code=$code",
            "personalnotify://pair?server=https%3A%2F%2Fnotify.example&code=short",
            "personalnotify://pair?server=https%3A%2F%2Fnotify.example&code=$code&code=$code",
            "https://notify.example/?code=$code"
        )) {
            assertThrows(IllegalArgumentException::class.java) { Pairing.parse(text) }
        }
    }
}
