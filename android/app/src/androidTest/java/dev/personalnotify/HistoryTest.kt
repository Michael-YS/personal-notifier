package dev.personalnotify

import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.core.app.ActivityScenario
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class HistoryTest {
    @Test fun activityStarts() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { assertFalse(it.isFinishing) }
        }
    }

    @Test fun historySurvivesReopen() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val name = "history-persistence-test.db"
        context.deleteDatabase(name)
        var database = Room.databaseBuilder(context, HistoryDatabase::class.java, name).build()
        try {
            val id = database.history().insert(HistoryItem(messageId = "persist", title = "标题", body = "内容", source = "test", sentAt = ""))
            database.close()
            database = Room.databaseBuilder(context, HistoryDatabase::class.java, name).build()
            assertEquals("内容", database.history().get(id)!!.body)
        } finally { database.close(); context.deleteDatabase(name) }
    }

    @Test fun duplicateReadAndDelete() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val database = Room.inMemoryDatabaseBuilder(context, HistoryDatabase::class.java).build()
        try {
            val dao = database.history()
            val item = HistoryItem(messageId = "same-id", title = "标题", body = "内容", source = "test", sentAt = "")
            val id = dao.insert(item)
            assertTrue(id > 0)
            assertEquals(-1L, dao.insert(item))
            dao.markRead(id)
            assertTrue(dao.get(id)!!.isRead)
            dao.delete(id)
            assertNull(dao.get(id))
        } finally { database.close() }
    }
}
