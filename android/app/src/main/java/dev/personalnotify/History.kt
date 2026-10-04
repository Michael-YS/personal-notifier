package dev.personalnotify

import android.content.Context
import androidx.room.*
import kotlinx.coroutines.flow.Flow

@Entity(tableName = "notifications", indices = [Index(value = ["messageId"], unique = true)])
data class HistoryItem(
    @PrimaryKey(autoGenerate = true) val localId: Long = 0,
    val messageId: String,
    val title: String,
    val body: String,
    val source: String,
    val sentAt: String,
    val receivedAt: Long = System.currentTimeMillis(),
    val isRead: Boolean = false,
)

@Dao
interface HistoryDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(item: HistoryItem): Long

    @Query("SELECT * FROM notifications ORDER BY localId DESC LIMIT 200")
    fun recent(): Flow<List<HistoryItem>>

    @Query("SELECT * FROM notifications WHERE localId = :id")
    suspend fun get(id: Long): HistoryItem?

    @Query("UPDATE notifications SET isRead = 1 WHERE localId = :id")
    suspend fun markRead(id: Long)

    @Query("DELETE FROM notifications WHERE localId = :id")
    suspend fun delete(id: Long)

    @Query("DELETE FROM notifications")
    suspend fun clear()
}

@Database(entities = [HistoryItem::class], version = 1, exportSchema = true)
abstract class HistoryDatabase : RoomDatabase() {
    abstract fun history(): HistoryDao

    companion object {
        @Volatile private var instance: HistoryDatabase? = null
        fun get(context: Context): HistoryDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext, HistoryDatabase::class.java, "history.db"
            ).build().also { instance = it }
        }
    }
}
