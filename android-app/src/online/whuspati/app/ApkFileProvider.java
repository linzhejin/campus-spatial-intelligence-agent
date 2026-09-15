package online.whuspati.app;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import android.text.TextUtils;

import java.io.File;
import java.io.FileNotFoundException;

/**
 * 极简 FileProvider（不依赖 androidx）：仅用于把应用内更新下载的 APK
 * 以 content:// 授权给系统安装器读取。
 *
 * URI: content://online.whuspati.app.fileprovider/apk/<白名单文件名>
 * 文件根目录固定为 getExternalFilesDir(Downloads)，文件名走白名单，
 * 杜绝路径穿越与任意文件暴露。
 */
public class ApkFileProvider extends ContentProvider {

    public static final String AUTHORITY = "online.whuspati.app.fileprovider";
    private static final String PATH_APK = "apk";
    private static final String APK_FILENAME = "whu-walker-update.apk";

    public static Uri uriFor(File apkFile) {
        return new Uri.Builder()
                .scheme("content")
                .authority(AUTHORITY)
                .appendPath(PATH_APK)
                .appendPath(apkFile.getName())
                .build();
    }

    private File resolveFile(Uri uri) {
        if (uri == null || !AUTHORITY.equals(uri.getAuthority())) return null;
        if (getContext() == null) return null;
        // content://authority/apk/<name>
        if (uri.getPathSegments() == null || uri.getPathSegments().size() != 2) return null;
        if (!PATH_APK.equals(uri.getPathSegments().get(0))) return null;
        String name = uri.getPathSegments().get(1);
        if (TextUtils.isEmpty(name) || name.contains("/") || name.contains("..")
                || !APK_FILENAME.equals(name)) {
            return null;
        }
        File dir = getContext().getExternalFilesDir(android.os.Environment.DIRECTORY_DOWNLOADS);
        if (dir == null) return null;
        File f = new File(dir, name);
        try {
            if (!f.getCanonicalPath().startsWith(dir.getCanonicalPath())) return null;
        } catch (Exception e) {
            return null;
        }
        return f.exists() ? f : null;
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) throw new FileNotFoundException("read-only provider");
        File f = resolveFile(uri);
        if (f == null) throw new FileNotFoundException("apk not allowed / missing");
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public String getType(Uri uri) {
        return "application/vnd.android.package-archive";
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection,
                        String[] selectionArgs, String sortOrder) {
        File f = resolveFile(uri);
        if (f == null) return null;
        if (projection == null) {
            projection = new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        }
        MatrixCursor cursor = new MatrixCursor(projection);
        Object[] row = new Object[projection.length];
        for (int i = 0; i < projection.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(projection[i])) {
                row[i] = f.getName();
            } else if (OpenableColumns.SIZE.equals(projection[i])) {
                row[i] = f.length();
            }
        }
        cursor.addRow(row);
        return cursor;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException("read-only provider");
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        return 0;
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        return 0;
    }
}
