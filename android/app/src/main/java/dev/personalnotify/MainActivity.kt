package dev.personalnotify

import android.Manifest
import android.app.*
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.widget.*
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.launch
import java.text.DateFormat
import java.util.Date
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions

class MainActivity : androidx.activity.ComponentActivity() {
    private val scanner = registerForActivityResult(ScanContract()) { result ->
        result.contents?.let { pairScanned(it) }
    }
    private var pairing = false
    private val preferences by lazy { getSharedPreferences("settings", MODE_PRIVATE) }
    private val statusListener = android.content.SharedPreferences.OnSharedPreferenceChangeListener { _, _ ->
        runOnUiThread { refreshStatus() }
    }
    private lateinit var status: TextView
    private val history by lazy { HistoryDatabase.get(this).history() }
    private val rows = mutableListOf<HistoryItem>()
    private val adapter by lazy {
        object : BaseAdapter() {
            override fun getCount() = rows.size
            override fun getItem(position: Int) = rows[position]
            override fun getItemId(position: Int) = rows[position].localId
            override fun getView(position: Int, convertView: View?, parent: ViewGroup?): View {
                val item = rows[position]
                return (convertView as? TextView ?: TextView(this@MainActivity)).apply {
                    setPadding(dp(16), dp(12), dp(16), dp(12))
                    textSize = 16f
                    text = "${if (item.isRead) "" else "● "}${item.title}\n${item.body.take(160)}\n${item.source} · ${DateFormat.getDateTimeInstance().format(Date(item.receivedAt))}"
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(8))
            if (Build.VERSION.SDK_INT >= 35) setOnApplyWindowInsetsListener { view, insets ->
                val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars())
                view.setPadding(dp(16) + bars.left, dp(16) + bars.top, dp(16) + bars.right, dp(8) + bars.bottom)
                insets
            }
        }
        root.addView(TextView(this).apply { text = "Personal Notify"; textSize = 26f })
        status = TextView(this).apply { setPadding(0, dp(8), 0, dp(8)) }
        root.addView(status)
        root.addView(Button(this).apply {
            text = "扫码配对"
            setOnClickListener {
                if (!pairing) scanner.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                    .setPrompt("扫描配对网页上的二维码").setBeepEnabled(false).setOrientationLocked(false))
            }
        })
        root.addView(Button(this).apply { text = "绑定设置 / 重新注册"; setOnClickListener { settingsDialog() } })
        root.addView(Button(this).apply {
            text = "通知权限设置"
            setOnClickListener {
                if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                    requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
                } else startActivity(Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, packageName))
            }
        })
        root.addView(Button(this).apply {
            text = "清空本地历史"
            setOnClickListener {
                AlertDialog.Builder(this@MainActivity).setMessage("删除手机上的全部通知历史？")
                    .setNegativeButton("取消", null).setPositiveButton("清空") { _, _ ->
                        lifecycleScope.launch { history.clear(); getSystemService(NotificationManager::class.java).cancelAll() }
                    }.show()
            }
        })
        root.addView(TextView(this).apply { text = "最近 200 条 · 点击查看详情；历史仅保存在本机" })
        val list = ListView(this).apply {
            adapter = this@MainActivity.adapter
            setOnItemClickListener { _, _, position, _ -> showItem(rows[position].localId) }
        }
        root.addView(list, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch { history.recent().collect { rows.clear(); rows.addAll(it); adapter.notifyDataSetChanged() } }
            }
        }
        Registration.enqueue(this)
        Registration.periodic(this)
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        openNotification(intent)
    }

    override fun onResume() { super.onResume(); refreshStatus() }

    override fun onStart() { super.onStart(); preferences.registerOnSharedPreferenceChangeListener(statusListener) }
    override fun onStop() { preferences.unregisterOnSharedPreferenceChangeListener(statusListener); super.onStop() }

    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); setIntent(intent); openNotification(intent) }

    private fun openNotification(intent: Intent) {
        val id = intent.getLongExtra("history_id", -1)
        intent.removeExtra("history_id")
        if (id != -1L) showItem(id)
    }

    private fun showItem(id: Long) {
        lifecycleScope.launch {
            val item = history.get(id) ?: return@launch
            history.markRead(id)
            getSystemService(NotificationManager::class.java).cancel(id.toString(), 0)
            AlertDialog.Builder(this@MainActivity).setTitle(item.title)
                .setMessage("${item.body}\n\n来源：${item.source}\n发送：${item.sentAt}\n接收：${DateFormat.getDateTimeInstance().format(Date(item.receivedAt))}")
                .setPositiveButton("关闭", null).setNegativeButton("删除") { _, _ -> lifecycleScope.launch { history.delete(id) } }.show()
        }
    }

    private fun refreshStatus() {
        val allowed = getSystemService(NotificationManager::class.java).areNotificationsEnabled()
        status.text = "${AppSettings(this).status}\n系统通知：${if (allowed) "已开启" else "未开启（收到的消息仍保存到历史）"}"
    }

    private fun pairScanned(contents: String) {
        val ticket = try { Pairing.parse(contents) } catch (_: Exception) {
            Toast.makeText(this, "不是有效的 Personal Notify 配对二维码", Toast.LENGTH_LONG).show()
            return
        }
        if (pairing) return
        AlertDialog.Builder(this).setTitle("配对设备")
            .setMessage("连接到：${ticket.server}\n配对成功后将使用这个服务接收通知，已有本地历史会保留。")
            .setNegativeButton("取消", null).setPositiveButton("配对") { _, _ ->
                pairing = true
                AppSettings(this).status("正在配对…")
                lifecycleScope.launch {
                    try {
                        val binding = Pairing.redeem(ticket)
                        AppSettings(this@MainActivity).save(binding.server, binding.device, binding.key)
                        Registration.enqueue(this@MainActivity)
                        Registration.periodic(this@MainActivity)
                        Toast.makeText(this@MainActivity, "配对成功，正在注册推送", Toast.LENGTH_LONG).show()
                    } catch (cancelled: kotlinx.coroutines.CancellationException) {
                        throw cancelled
                    } catch (_: Exception) {
                        AppSettings(this@MainActivity).status("配对失败，请重新生成二维码再试；原绑定未改变")
                        Toast.makeText(this@MainActivity, "配对失败：二维码可能已使用、过期，或网络不可用", Toast.LENGTH_LONG).show()
                    } finally { pairing = false; refreshStatus() }
                }
            }.show()
    }

    private fun settingsDialog() {
        val settings = AppSettings(this)
        val form = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), 0, dp(20), 0) }
        fun field(label: String, value: String, secret: Boolean = false): EditText {
            form.addView(TextView(this).apply { text = label })
            return EditText(this@MainActivity).apply {
                setSingleLine(true)
                inputType = if (secret) InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD else InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
                setText(value)
                form.addView(this)
            }
        }
        val server = field("服务端 HTTPS 地址", settings.server)
        val device = field("设备名", settings.device)
        val key = field("该设备的绑定密钥", settings.key, true)
        val dialog = AlertDialog.Builder(this).setTitle("绑定设置").setView(form)
            .setNegativeButton("取消", null).setPositiveButton("保存并注册", null).create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                try {
                    settings.save(server.text.toString().trim(), device.text.toString().trim(), key.text.toString().trim())
                    Registration.enqueue(this); Registration.periodic(this); refreshStatus(); dialog.dismiss()
                } catch (error: Exception) { Toast.makeText(this, error.message ?: "配置无效", Toast.LENGTH_LONG).show() }
            }
        }
        dialog.show()
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
}
