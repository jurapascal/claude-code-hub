package io.github.jurapascal.claudehub

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebResourceResponse
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

/**
 * Okno kolem hubu.
 *
 * Celá aplikace je jeden WebView — hub je webová appka a tohle je jen její
 * rám, který navíc umí to, co prohlížeč na telefonu neumí: vlastní ikonu,
 * hardwarové tlačítko zpět jako návrat v hubu a přílohu ze souborů.
 */
class HubActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_URL = "url"
    }

    private lateinit var web: WebView
    private lateinit var home: String
    private var filePick: ValueCallback<Array<Uri>>? = null

    private val chooser = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()) { result ->
        val callback = filePick ?: return@registerForActivityResult
        filePick = null
        callback.onReceiveValue(
            WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data))
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        home = intent.getStringExtra(EXTRA_URL) ?: Hub.saved(this) ?: run {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        setContentView(R.layout.activity_hub)
        web = findViewById(R.id.web)

        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true          // hub si do localStorage ukládá historii
            mediaPlaybackRequiresUserGesture = false
            useWideViewPort = true
            loadWithOverviewMode = false
            // Hub si velikost písma řídí sám podle šířky okna; systémové
            // zvětšení textu by mu rozsypalo terminál na sloupce navíc.
            textZoom = 100
        }
        // Token po spárování drží cookie — bez tohohle by se po každém
        // spuštění muselo skenovat znovu.
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false)

        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView, request: WebResourceRequest): Boolean {
                val target = request.url.toString()
                if (Hub.sameHost(home, target)) return false
                // Odkaz ven z hubu (dokumentace, GitHub) patří do prohlížeče.
                startActivity(Intent(Intent.ACTION_VIEW, request.url))
                return true
            }

            /* Token se dá v hubu vyměnit („Odpojit telefon"). Server pak na
               úvodní stránku odpoví 403 a tady je jediné rozumné vyústění
               nabídnout spárování znovu — jinak by aplikace zůstala viset na
               stránce s vysvětlením, kterou nejde nijak opustit. */
            override fun onReceivedHttpError(
                view: WebView,
                request: WebResourceRequest,
                response: WebResourceResponse) {
                if (!request.isForMainFrame || response.statusCode != 403) return
                Hub.forget(this@HubActivity)
                startActivity(Intent(this@HubActivity, SetupActivity::class.java))
                finish()
            }
        }

        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams): Boolean {
                filePick?.onReceiveValue(null)
                filePick = callback
                return runCatching { chooser.launch(params.createIntent()); true }
                    .getOrElse { filePick = null; false }
            }
        }

        // Systémové zpět prochází historii hubu; na jeho začátku zavře appku.
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else finish()
            }
        })

        if (state == null) web.loadUrl(home) else web.restoreState(state)
    }

    override fun onSaveInstanceState(out: Bundle) {
        super.onSaveInstanceState(out)
        web.saveState(out)
    }
}
